"""传感器接入层：把「现场读数」和「滤波器」解耦。

真实现场只需要实现 :class:`SensorDriver` 的 ``read()``——返回
``{Q_gas, Q_ch4, pH}`` 三个量测——就能接到现有的更正回路里。
现在提供两个实现：

* :class:`SimulatedDriver` —— 用一个内部装置当"现场"，叠上传感器误差
  （比例偏差、噪声、丢数），用来验证整条链路。
* :class:`ManualDriver` —— 由页面直接填读数，相当于人工抄表。

以后接 Modbus / OPC UA 时，新写一个 ``read()`` 即可，上层不用改。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from online import OnlineSession


class SensorDriver(abc.ABC):
    """Anything that can hand back one reading of the plant."""

    kind: str = "abstract"

    @abc.abstractmethod
    def read(self) -> dict[str, float]:
        """Return ``{Q_gas, Q_ch4, pH}`` for the current moment."""

    def describe(self) -> dict[str, Any]:
        """Human-readable description for the page."""
        return {"kind": self.kind}


@dataclass
class SimulatedDriver(SensorDriver):
    """A truth plant behind a sensor with calibration error."""

    session: OnlineSession
    bias: float = 0.0          # 比例偏差：+0.05 = 读数比真值高 5%
    ph_bias: float = 0.0       # pH 是绝对值偏差
    dropout: float = 0.0       # 丢数概率（返回 None 的位）
    seed: int = 11

    kind = "simulated"

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.last_truth: dict[str, float] = {}

    def read(self) -> dict[str, float]:
        """Advance the truth plant one step and return the sensor's view.

        时间由调用方（``SensorHub`` → ``OnlineSession.ingest``）推进；这里只负责
        让"现场装置"往前走一格并读数，否则一次取数会吃掉两小时的仿真时间。
        """
        session = self.session
        res = session.truth.simulate(duration=session.cfg.dt, dt=session.cfg.dt, save_interval=session.cfg.dt)
        c = res[-1]["components"]["main_digester"]

        truth = {"Q_gas": float(c["Q_gas"]), "Q_ch4": float(c["Q_ch4"]), "pH": float(c["pH"])}
        self.last_truth = truth

        y: dict[str, float] = {}
        for k, v in truth.items():
            if self.dropout and self.rng.random() < self.dropout:
                continue
            if k == "pH":
                y[k] = float(v + self.ph_bias + session.cfg.noise * 0.5 * self.rng.standard_normal())
            else:
                y[k] = float(v * (1.0 + self.bias) * (1.0 + session.cfg.noise * self.rng.standard_normal()))
        return y

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "bias_pct": round(self.bias * 100, 1),
            "ph_bias": round(self.ph_bias, 2),
            "dropout_pct": round(self.dropout * 100, 1),
            "note": "内部装置模拟现场，读数叠加了系统偏差与噪声",
        }


@dataclass
class ManualDriver(SensorDriver):
    """Readings typed in by hand (a shift operator copying the gauge)."""

    kind = "manual"
    last: dict[str, float] = field(default_factory=dict)

    def read(self) -> dict[str, float]:
        return dict(self.last)

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "note": "由页面直接填入读数"}


@dataclass
class SensorHub:
    """Wires a driver to the correction filter."""

    session: OnlineSession
    driver: SensorDriver

    def poll(self, ingest: bool = True) -> dict[str, Any]:
        """Take one reading and (optionally) feed it to the filter."""
        reading = self.driver.read()
        snap = self.session.ingest(reading) if ingest and reading else self.session.snapshot()
        return self._payload(reading, snap, ingest)

    def push(self, reading: dict[str, float], ingest: bool = True) -> dict[str, Any]:
        """Inject an external reading (manual entry, file replay, field device)."""
        clean = {k: float(reading[k]) for k in ("Q_gas", "Q_ch4", "pH") if k in reading}
        if not clean:
            raise ValueError("至少要给出 Q_gas / Q_ch4 / pH 中的一个")
        snap = self.session.ingest(clean) if ingest else self.session.snapshot()
        return self._payload(clean, snap, ingest)

    def _payload(self, reading: dict[str, float], snap: dict[str, Any], ingested: bool) -> dict[str, Any]:
        truth = getattr(self.driver, "last_truth", {})
        errs = {}
        for k, v in reading.items():
            if k in truth and abs(truth[k]) > 1e-9:
                errs[k] = round((v - truth[k]) / truth[k] * 100.0, 2) if k != "pH" else round(v - truth[k], 3)
        return {
            "ok": True,
            "driver": self.driver.describe(),
            "reading": {k: round(float(v), 3) for k, v in reading.items()},
            "truth": {k: round(float(v), 3) for k, v in truth.items()},
            "reading_error": errs,
            "ingested": bool(ingested),
            "snapshot": snap,
        }


def make_driver(session: OnlineSession, spec: dict[str, Any]) -> SensorDriver:
    """Build a driver from the request payload."""
    kind = str(spec.get("kind") or "simulated")
    if kind == "manual":
        return ManualDriver()
    return SimulatedDriver(
        session=session,
        bias=float(spec.get("bias", 0.0)),
        ph_bias=float(spec.get("ph_bias", 0.0)),
        dropout=float(spec.get("dropout", 0.0)),
        seed=int(spec.get("seed", 11)),
    )
