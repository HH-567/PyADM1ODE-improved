"""在线更正会话：UKF 逐步状态更正 + OnlineCalibrator 窗口参数更正。

设计要点（每一条都对着实测踩过的坑）：

* 量测键名必须是 ``Q_gas`` / ``Q_ch4`` / ``pH``（大写），与观测器内部约定一致；
  小写键会被当成未知量测，滤波器看似在跑却不更新。
* ``MeasurementData`` 需要 ``timestamp`` 列（datetime），传数字时间会在标定里
  炸出 ``int - timedelta``。
* 参数被 ``apply_calibration`` 改动后，sigma 点仍基于旧模型，必须重建滤波器
  再续跑，否则新息带系统偏差。
* UKF 单步约 6 秒（87 个 sigma 点、每点推进一遍机理模型），所以状态按小时更正，
  参数按天窗口更正，两者节奏不同。
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1.core import set_solver_backend_override
from pyadm1ode_calibration import MeasurementData, OnlineCalibrator
from pyadm1ode_estimation.estimation import InputSpec, build_ukf

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]
T_AD = 42.0 + 273.15


@dataclass
class OnlineConfig:
    """One-touch settings for an online run."""

    dt: float = 1.0
    warmup_days: float = 30.0
    prior_scale: float = 0.6
    noise: float = 0.02
    calib_every: int = 6
    seed: int = 7
    backend: str = "scipy"
    # k_hyd_ch 在稳态上几乎不敏感（单因素 |S|≈0.03），漂移了也看不出来；
    # k_m_ac 是筛选实验里排第一的参数（|S|≈3.0），漂移会立刻反映到产气。
    parameter: str = "k_m_ac"
    # 自带判据看残差标准差，对恒定偏置视而不见；这条按均值残差判漂移。
    bias_threshold: float = 0.03


@dataclass
class OnlineSession:
    """A live plant, a filter watching it, and a window calibrator on top."""

    cfg: OnlineConfig = field(default_factory=OnlineConfig)

    def __post_init__(self) -> None:
        warnings.filterwarnings("ignore", message=".*TAC <= 0.*")
        set_solver_backend_override(self.cfg.backend)
        self.rng = np.random.default_rng(self.cfg.seed)
        days = self.cfg.warmup_days + 2.0
        self.truth = self._build(days)
        self.est = self._build(days)
        self.truth.simulate(duration=self.cfg.warmup_days, dt=self.cfg.dt, save_interval=1.0)
        self.est.simulate(duration=self.cfg.warmup_days, dt=self.cfg.dt, save_interval=1.0)
        self.n_state = len(self.est.components["main_digester"].adm1_state)
        self._build_filter()
        self.calibrator = OnlineCalibrator(self.est, verbose=False)
        self.t = 0.0
        self.rows: list[dict[str, Any]] = []
        self.series: dict[str, list[float]] = {
            "t": [], "err": [], "nis": [], "feed0": [], "feed1": [], "truth0": [], "truth1": [],
        }
        self.events: list[dict[str, Any]] = []
        self.param = float(self.est.components["main_digester"].adm1._kinetic[self.cfg.parameter])
        self.steps = 0
        self.cost_ms = 0.0
        # 每个时刻的估计状态，供触发判据回到窗口起点复核
        self.x_hist: list[np.ndarray] = [np.asarray(self.ukf.x_hat[: self.n_state], dtype=float).copy()]

    # -- setup -----------------------------------------------------------
    def _build(self, days: float) -> BiogasPlant:
        fs = Feedstock(SUBS, feeding_freq=24, total_simtime=days + 2.0)
        plant = BiogasPlant(f"online-{id(self)}")
        PlantConfigurator(plant, fs).add_digester(
            digester_id="main_digester",
            V_liq=1050.0,
            V_gas=150.0,
            T_ad=T_AD,
            Q_substrates=FLOWS,
            k_L_a=200.0,
        )
        plant.initialize()
        return plant

    def _build_filter(self) -> None:
        old_feed: list[float] | None = None
        if getattr(self, "ukf", None) is not None:
            old_feed = [float(self.ukf.x_hat[self.n_state]), float(self.ukf.x_hat[self.n_state + 1])]
        self.ukf = build_ukf(
            self.est,
            "main_digester",
            substrates=[
                InputSpec("maize", 0, FLOWS[0] * self.cfg.prior_scale, process_noise_std=1.5),
                InputSpec("swine", 1, FLOWS[1] * self.cfg.prior_scale, process_noise_std=1.5),
            ],
            sensors=("q_gas", "q_ch4", "ph"),
        )
        if old_feed is not None:
            # 参数变了，状态从装置现态重新起步，但保留已收敛的进料估计，
            # 不让一次重标定把滤波器的进展清零。
            state = np.asarray(self.est.components["main_digester"].adm1_state, dtype=float)
            self.ukf.x_hat[: self.n_state] = state
            self.ukf.x_hat[self.n_state] = old_feed[0]
            self.ukf.x_hat[self.n_state + 1] = old_feed[1]

    # -- one measurement -------------------------------------------------
    def _measure(self) -> dict[str, float]:
        res = self.truth.simulate(duration=self.cfg.dt, dt=self.cfg.dt, save_interval=self.cfg.dt)
        comp = res[-1]["components"]["main_digester"]
        noise = self.cfg.noise
        ph = float(comp["pH"])
        self.t += self.cfg.dt
        return {
            "Q_gas": float(comp["Q_gas"]) * (1.0 + noise * self.rng.standard_normal()),
            "Q_ch4": float(comp["Q_ch4"]) * (1.0 + noise * self.rng.standard_normal()),
            "pH": ph + 0.01 * noise * 50 * self.rng.standard_normal(),
        }

    # -- main loop -------------------------------------------------------
    def ingest(self, y: dict[str, float]) -> dict[str, Any]:
        """Update the filter with an **external** reading.

        This is the seam a real sensor plugs into: whatever produces
        ``{Q_gas, Q_ch4, pH}`` (a driver, a CSV replay, a manual entry) calls
        this instead of ``step()``, which would generate the reading itself.
        """
        t0 = time.perf_counter()
        self.x_hist.append(np.asarray(self.ukf.x_hat[: self.n_state], dtype=float).copy())
        self.ukf.predict(self.cfg.dt)
        upd = self.ukf.update(
            {k: float(y[k]) for k in ("Q_gas", "Q_ch4", "pH") if k in y},
            t=self.t + self.cfg.dt,
            equilibration_dt=self.cfg.dt,
        )
        self.t += self.cfg.dt
        self.steps += 1
        self._record(y, upd)
        if self.cfg.calib_every and self.steps % self.cfg.calib_every == 0:
            self._maybe_calibrate()
        self.cost_ms = (time.perf_counter() - t0) * 1000.0
        return self.snapshot(1)

    def step(self, n: int = 1) -> dict[str, Any]:
        """Advance ``n`` measurements: filter every one, calibrate on schedule."""
        t0 = time.perf_counter()
        ran = 0
        for _ in range(max(1, min(int(n), 8))):
            y = self._measure()  # 会推进真值装置并把 self.t 往前挪一格
            self.x_hist.append(np.asarray(self.ukf.x_hat[: self.n_state], dtype=float).copy())
            self.ukf.predict(self.cfg.dt)
            upd = self.ukf.update(
                y, t=self.t, equilibration_dt=self.cfg.dt
            )
            self.steps += 1
            ran += 1
            self._record(y, upd)
            if self.cfg.calib_every and self.steps % self.cfg.calib_every == 0:
                self._maybe_calibrate()

        self.cost_ms = (time.perf_counter() - t0) * 1000.0
        return self.snapshot(ran)

    def _record(self, y: dict[str, float], upd: Any) -> None:
        """Append one accepted measurement to the series and the window."""
        hat = np.asarray(self.ukf.x_hat[: self.n_state], dtype=float)
        ref = np.asarray(self.truth.components["main_digester"].adm1_state, dtype=float)
        mask = np.abs(ref) > 1e-6
        err = float(np.median(np.abs((hat[mask] - ref[mask]) / ref[mask]))) if mask.any() else float("nan")

        nis = float("nan")
        nis_raw = getattr(upd, "nis", None)
        try:
            if isinstance(nis_raw, dict):
                vals = [float(v) for v in nis_raw.values() if np.isfinite(v)]
                nis = float(np.mean(vals)) if vals else float("nan")
            elif nis_raw is not None:
                arr = np.asarray(nis_raw, dtype=float).ravel()
                arr = arr[np.isfinite(arr)]
                nis = float(np.mean(arr)) if arr.size else float("nan")
        except Exception:  # noqa: BLE001 - NIS 只是指标，取不到不该中断
            nis = float("nan")

        self.series["t"].append(self.t)
        self.series["err"].append(err)
        self.series["nis"].append(nis)
        self.series["feed0"].append(float(self.ukf.x_hat[self.n_state]))
        self.series["feed1"].append(float(self.ukf.x_hat[self.n_state + 1]))
        self.series["truth0"].append(FLOWS[0])
        self.series["truth1"].append(FLOWS[1])

        self.rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=len(self.rows) * self.cfg.dt),
                "Q_gas": y["Q_gas"],
                "Q_ch4": y["Q_ch4"],
                "pH": y["pH"],
            }
        )

    def _sync_plant_state(self, state_vector: np.ndarray | None = None) -> None:
        """Write a state vector into the estimator plant.

        Both trigger checks (the package's variance and our bias metric) simulate
        the plant **from the start of the window**. Handing them the current
        state makes the replay run forward from "now" while the measurements sit
        in the past, and every window then looks biased no matter the parameters.
        """
        try:
            comp = self.est.components["main_digester"]
            state = comp.get_state()  # dict：set_state 要的是这个形状，不是裸向量
            if state_vector is None:
                state_vector = np.asarray(self.ukf.x_hat[: self.n_state], dtype=float)
            state["adm1_state"] = np.asarray(state_vector, dtype=float).tolist()
            comp.set_state(state)
        except Exception:  # noqa: BLE001 - 同步失败不该中断在线循环
            pass

    def _bias_trigger(self, meas: MeasurementData) -> tuple[bool, float]:
        """Normalised *mean* residual over the window.

        The package's own trigger uses ``std(residual)``, which is blind to a
        constant parameter offset: the residual has a bias but almost no
        scatter, so its standard deviation stays low and the check never fires.
        A drift shows up in the mean, so watch that instead.
        """
        try:
            params = self.calibrator._get_current_parameters()
            out = self.calibrator.simulator.simulate_with_parameters(params, meas)
            if "Q_ch4" not in out:
                return False, 0.0
            m = np.asarray(meas.get_measurement("Q_ch4").values, dtype=float)
            s = np.atleast_1d(np.asarray(out["Q_ch4"], dtype=float))
            n = min(len(m), len(s))
            m, s = m[:n], s[:n]
            ok = ~(np.isnan(m) | np.isnan(s))
            if not np.any(ok):
                return False, 0.0
            bias = abs(float(np.mean(m[ok] - s[ok]))) / (float(np.mean(np.abs(m[ok]))) + 1e-10)
            return bias > self.cfg.bias_threshold, bias
        except Exception:  # noqa: BLE001 - 判据算不出来就当没触发
            return False, 0.0

    def _maybe_calibrate(self) -> None:
        """Trigger check, then a bounded window recalibration (never per step)."""
        try:
            window = self.rows[-max(self.cfg.calib_every * 4, 12):]
            meas = MeasurementData(pd.DataFrame(window))
            # 回到窗口起点再跑判据，跑完再回到当前估计
            start_idx = max(0, len(self.rows) - len(window))
            if start_idx < len(self.x_hist):
                self._sync_plant_state(self.x_hist[start_idx])
            need_pkg, reason_pkg = self.calibrator.should_recalibrate(meas, objectives=["Q_ch4"])
            need_bias, bias = self._bias_trigger(meas)
            need = bool(need_pkg or need_bias)
            reason = (
                f"残差偏置 {bias:.1%} > {self.cfg.bias_threshold:.0%}"
                if need_bias
                else f"自带判据：{reason_pkg} ｜ 残差偏置 {bias:.1%}"
            )
            ev = {"t": self.t, "need": need, "reason": reason, "changed": None, "ms": 0.0,
                  "kind": "trigger", "bias": round(bias, 4)}
            self._sync_plant_state()
            if need:
                t0 = time.perf_counter()
                before = float(self.est.components["main_digester"].adm1._kinetic[self.cfg.parameter])
                result = self.calibrator.calibrate(
                    meas,
                    parameters=[self.cfg.parameter],
                    objectives=["Q_ch4"],
                    method="nelder_mead",
                    max_iterations=12,
                    time_window=len(meas.data),
                    max_parameter_change=0.2,
                )
                self.calibrator.apply_calibration(result)
                after = float(self.est.components["main_digester"].adm1._kinetic[self.cfg.parameter])
                ev["ms"] = (time.perf_counter() - t0) * 1000.0
                ev["changed"] = {"param": self.cfg.parameter, "before": before, "after": after}
                ev["kind"] = "calibrated"
                self.param = after
                self._build_filter()
            self.events.append(ev)
        except Exception as exc:  # noqa: BLE001 - 在线循环不能因为一次标定崩掉
            self.events.append(
                {"t": self.t, "need": False, "reason": f"跳过：{type(exc).__name__}",
                 "changed": None, "ms": 0.0, "kind": "trigger"}
            )

    def perturb(self, factor: float = 1.4) -> dict[str, Any]:
        """Drift the *truth* plant's parameter, leaving the filter on the old value.

        This is the demo's "reality changed" button: the model still predicts with
        the old constant, so the residual grows and the next checkpoint has
        something real to react to.
        """
        adm = self.truth.components["main_digester"].adm1
        cur = adm._kinetic.get(self.cfg.parameter)
        before = float(np.ravel(cur)[0]) if isinstance(cur, (list, np.ndarray)) else float(cur)
        after = before * float(factor)
        if isinstance(cur, (list, np.ndarray)):
            adm._kinetic[self.cfg.parameter] = np.full_like(np.asarray(cur, dtype=float), after)
        else:
            adm._kinetic[self.cfg.parameter] = after
        self.events.append(
            {
                "t": self.t,
                "need": None,
                "reason": f"真值参数漂移 ×{factor:g}",
                "changed": {"param": self.cfg.parameter, "before": before, "after": after},
                "ms": 0.0,
                "kind": "perturb",
            }
        )
        return self.snapshot()

    # -- payload ---------------------------------------------------------
    def snapshot(self, ran: int = 0) -> dict[str, Any]:
        """Everything the page needs for one refresh."""
        return {
            "ok": True,
            "t": self.t,
            "steps": self.steps,
            "ran": ran,
            "cost_ms": round(self.cost_ms, 1),
            "state_error": self.series["err"][-1] if self.series["err"] else None,
            "nis": self.series["nis"][-1] if self.series["nis"] else None,
            "feed_est": [
                self.series["feed0"][-1] if self.series["feed0"] else None,
                self.series["feed1"][-1] if self.series["feed1"] else None,
            ],
            "feed_true": FLOWS[:2],
            "param": {"name": self.cfg.parameter, "value": round(self.param, 6)},
            "series": self.series,
            "events": self.events[-8:],
            "config": {
                "dt": self.cfg.dt,
                "prior_scale": self.cfg.prior_scale,
                "noise": self.cfg.noise,
                "calib_every": self.cfg.calib_every,
            },
        }

    def close(self) -> None:
        """Drop the reference; the GC reclaims both plants."""
        self.rows.clear()
