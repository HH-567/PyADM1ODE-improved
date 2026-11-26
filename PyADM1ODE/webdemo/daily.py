"""按天推进入口：每天选加什么料、加多少，然后走一天。

设计要点：
* 换料用 ``Digester.set_feedstock(..., rebuild_state=False)``——它会重接进料
  却保留 41 维状态；用 ``rebuild_state=True`` 会把消化器重置回初值，历史全丢。
* 一天一步（``plant.step(dt=1)``），所以「第几天」就是推进的步数。
* OLR / HRT 是给现场看的工程量，按 TS 与灰分估算，只作参考。
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1.core import set_solver_backend_override

import substrates as sub_store

V_LIQ = 1050.0
V_GAS = 150.0
K_LA = 200.0


def _num(value: Any, digits: int) -> float | None:
    """JSON cannot carry NaN; the UI wants ``null`` for "no value"."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if np.isfinite(v) else None


@dataclass
class DailyConfig:
    """Reactor settings that stay fixed for a session."""

    substrate_ids: list[str] = field(default_factory=lambda: ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"])
    flows: list[float] = field(default_factory=lambda: [11.4, 6.1, 5.0])
    v_liq: float = V_LIQ
    v_gas: float = V_GAS
    t_c: float = 42.0
    k_la: float = K_LA
    seed: int = 7


def olr_hrt(ids: list[str], flows: list[float], v_liq: float) -> tuple[float, float]:
    """Organic loading rate [kg VS/(m³·d)] and hydraulic retention time [d]."""
    vs = 0.0
    for sid, q in zip(ids, flows, strict=False):
        try:
            p = sub_store.make_params(sid)
            vs += float(q) * float(p.TS) * max(0.0, 1.0 - float(p.fRA))
        except Exception:  # noqa: BLE001
            continue
    total_q = float(sum(flows)) or 1e-9
    return vs / max(v_liq, 1e-9), v_liq / total_q


@dataclass
class DailySession:
    """A plant that the user feeds one day at a time."""

    cfg: DailyConfig = field(default_factory=DailyConfig)

    def __post_init__(self) -> None:
        warnings.filterwarnings("ignore", message=".*TAC <= 0.*")
        set_solver_backend_override("scipy")
        self.plant = self._build(self.cfg.substrate_ids, self.cfg.flows)
        self.ids = list(self.cfg.substrate_ids)
        self.flows = list(self.cfg.flows)
        self.day = 0
        self.t0 = time.perf_counter()
        self.t_days = 0.0
        self.hour = 0
        self.ukf = None
        self.est = None
        self.last_reco: dict[str, Any] | None = None
        self.drifts: list[dict[str, Any]] = []
        # 跳跃回某一天要从这里重放
        self.initial_state = self.plant.components["main_digester"].get_state()
        self.hourly: dict[str, list[float]] = {
            "t": [], "q_gas": [], "q_ch4": [], "ph": [], "truth_gas": [], "err": [], "nis": [],
        }
        self.series: dict[str, list[float]] = {
            "day": [], "t_days": [], "q_gas": [], "q_ch4": [], "ch4_pct": [], "ph": [],
            "vfa": [], "tac": [], "q_total": [], "olr": [], "hrt": [],
        }
        self.history: list[dict[str, Any]] = []

    # -- setup ---------------------------------------------------------
    def _build(self, ids: list[str], flows: list[float]) -> BiogasPlant:
        params = [sub_store.make_params(i) for i in ids]
        fs = Feedstock(params, feeding_freq=24, total_simtime=400)
        plant = BiogasPlant("daily")
        PlantConfigurator(plant, fs).add_digester(
            digester_id="main_digester",
            V_liq=self.cfg.v_liq,
            V_gas=self.cfg.v_gas,
            T_ad=self.cfg.t_c + 273.15,
            Q_substrates=flows,
            k_L_a=self.cfg.k_la,
        )
        plant.initialize()
        return plant

    # -- one day -------------------------------------------------------
    def advance(self, feed: dict[str, float] | None = None) -> dict[str, Any]:
        """Set the day's feed, run one day, and record the result."""
        if feed:
            ids = [k for k, v in feed.items() if float(v) > 0]
            flows = [float(feed[k]) for k in ids]
            if not ids:
                raise ValueError("每天至少要加一种料")
            params = [sub_store.make_params(i) for i in ids]
            fs = Feedstock(params, feeding_freq=24, total_simtime=400)
            comp = self.plant.components["main_digester"]
            comp.set_feedstock(fs, Q_substrates=flows, rebuild_state=False)
            self.ids, self.flows = ids, flows

        out = self.plant.step(dt=1.0)
        c = out["main_digester"]
        self.day += 1
        self.t_days += 1.0

        q_gas = float(c.get("Q_gas", np.nan))
        self._last_gas = q_gas
        q_ch4 = float(c.get("Q_ch4", np.nan))
        ph = float(c.get("pH", np.nan))
        vfa = float(c.get("VFA", np.nan))
        tac = float(c.get("TAC", np.nan))
        olr, hrt = olr_hrt(self.ids, self.flows, self.cfg.v_liq)

        self.series["day"].append(self.day)
        self.series["t_days"].append(round(self.t_days, 3))
        self.series["q_gas"].append(q_gas)
        self.series["q_ch4"].append(q_ch4)
        self.series["ch4_pct"].append(q_ch4 / q_gas * 100.0 if q_gas > 0 else float("nan"))
        self.series["ph"].append(ph)
        self.series["vfa"].append(vfa)
        self.series["tac"].append(tac)
        self.series["q_total"].append(float(sum(self.flows)))
        self.series["olr"].append(olr)
        self.series["hrt"].append(hrt)

        self.history.append({"day": self.day, "feed": dict(zip(self.ids, self.flows, strict=False))})
        return self.snapshot()

    # -- 工作区存取 ------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        """Serialise the whole session, plant state included.

        "第几天" alone cannot reproduce the plant: ADM1 is a 41-state dynamical
        system, so reopening without the state vector would be a different
        digester. The state dict is the important half of this payload.
        """
        comp = self.plant.components["main_digester"]
        return {
            "config": {
                "substrate_ids": list(self.cfg.substrate_ids),
                "flows": list(self.cfg.flows),
                "v_liq": self.cfg.v_liq,
                "v_gas": self.cfg.v_gas,
                "t_c": self.cfg.t_c,
                "k_la": self.cfg.k_la,
                "seed": self.cfg.seed,
            },
            "day": self.day,
            "t_days": round(self.t_days, 4),
            "hour": self.hour,
            "hourly": self.hourly,
            "filter_x": (np.asarray(self.ukf.x_hat, dtype=float).tolist() if getattr(self, "ukf", None) is not None else None),
            "ids": list(self.ids),
            "flows": list(self.flows),
            "series": self.series,
            "history": self.history,
            "plant_state": comp.get_state(),
            "initial_state": self.initial_state,
            "saved_at": time.time(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DailySession":
        """Rebuild a session from :meth:`to_payload`."""
        cfg_raw = dict(payload.get("config") or {})
        cfg = DailyConfig(**{k: v for k, v in cfg_raw.items() if k in DailyConfig.__dataclass_fields__})
        self = cls(cfg)
        comp = self.plant.components["main_digester"]
        state = payload.get("plant_state")
        if state:
            comp.set_state(state)
        ids = list(payload.get("ids") or cfg.substrate_ids)
        flows = list(payload.get("flows") or cfg.flows)
        if ids and any(float(f) > 0 for f in flows):
            self.set_feed(dict(zip(ids, flows, strict=False)))
        self.day = int(payload.get("day") or 0)
        self.t_days = float(payload.get("t_days") or self.day)
        self.hour = int(payload.get("hour") or 0)
        self._saved_x = payload.get("filter_x")
        self.initial_state = payload.get("initial_state") or self.plant.components["main_digester"].get_state()
        if payload.get("hourly"):
            self.hourly = {k: list(v) for k, v in payload["hourly"].items() if k in self.hourly}
        if payload.get("series"):
            self.series = {k: list(v) for k, v in payload["series"].items() if k in self.series}
        if not self.series.get("t_days"):
            self.series["t_days"] = [float(i + 1) for i in range(len(self.series["day"]))]
        if payload.get("history"):
            self.history = list(payload["history"])
        return self

    # -- 推荐 & 多方案 --------------------------------------------------
    def set_feed(self, feed: dict[str, float]) -> None:
        """Apply a blend without stepping a day."""
        ids = [k for k, v in feed.items() if float(v) > 0]
        flows = [float(feed[k]) for k in ids]
        if not ids:
            raise ValueError("每天至少要加一种料")
        params = [sub_store.make_params(i) for i in ids]
        fs = Feedstock(params, feeding_freq=24, total_simtime=400)
        self.plant.components["main_digester"].set_feedstock(fs, Q_substrates=flows, rebuild_state=False)
        self.ids, self.flows = ids, flows

    def recommend(
        self,
        days: int = 30,
        limits: dict[str, float] | None = None,
        adjustable: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Optimise the blend from the plant's **current** state."""
        import optimize as opt_mod

        state = self.plant.components["main_digester"].get_state()
        lm = opt_mod.Limits(**{k: float(v) for k, v in (limits or {}).items()})
        rec = opt_mod.optimize(
            ids=self.ids,
            current=self.flows,
            days=days,
            t_c=self.cfg.t_c,
            limits=lm,
            base_state=state,
            # 给了名单就按名单来：不在名单里的算锁定，不能默认放开
            adjustable=(None if adjustable is None else [bool(adjustable.get(sid, False)) for sid in self.ids]),
        )
        return rec

    def auto_recommended(
        self,
        days: int = 7,
        reopt_every: int = 7,
        days_per_eval: int = 30,
        limits: dict[str, float] | None = None,
        adjustable: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Keep feeding by the recommendation, re-optimising every N days."""
        applied: list[dict[str, Any]] = []
        for i in range(int(days)):
            if i % max(1, int(reopt_every)) == 0:
                rec = self.recommend(days=days_per_eval, limits=limits, adjustable=adjustable)
                self.set_feed(dict(zip(self.ids, rec["recommended"]["flows"], strict=False)))
                self.last_reco = {
                    "day": self.day + 1,
                    "ids": list(self.ids),
                    "names": [sub_store.zh_name(i) for i in self.ids],
                    "current": list(rec["current"]["flows"]),
                    "recommended": list(rec["recommended"]["flows"]),
                    "q_ch4_now": rec["current"]["q_ch4"],
                    "q_ch4_rec": rec["recommended"]["q_ch4"],
                    "gain_pct": rec["gain_pct"],
                    "binding": rec["binding"],
                    "violations": rec["recommended"]["violations"],
                }
                applied.append(
                    {
                        "day": self.day + 1,
                        "flows": rec["recommended"]["flows"],
                        "q_ch4": rec["recommended"]["q_ch4"],
                        "binding": rec["binding"],
                        "gain_pct": rec["gain_pct"],
                    }
                )
            self.advance()
        snap = self.snapshot()
        snap["applied"] = applied
        return snap

    def compare(self, scenarios: list[dict[str, Any]], days: int = 30) -> dict[str, Any]:
        """Run several blends from the same starting state and return their curves."""
        base = self.plant.components["main_digester"].get_state()
        results = []
        for sc in scenarios:
            flows = [float(v) for v in sc.get("flows") or []]
            label = str(sc.get("label") or "方案")
            if len(flows) != len(self.ids):
                raise ValueError(f"{label}：配料项数与会话底物数不一致")
            if not any(f > 0 for f in flows):
                continue
            plant = self._build(self.ids, flows)
            comp = plant.components["main_digester"]
            comp.set_state(base)
            params = [sub_store.make_params(i) for i in self.ids]
            fs = Feedstock(params, feeding_freq=24, total_simtime=400)
            comp.set_feedstock(fs, Q_substrates=flows, rebuild_state=False)

            ser = {"day": [], "q_gas": [], "q_ch4": [], "ch4_pct": [], "ph": [], "vfa_tac": [], "olr": [], "hrt": []}
            for d in range(int(days)):
                c = plant.step(dt=1.0)["main_digester"]
                tac = float(c.get("TAC", 0.0))
                vfa = float(c.get("VFA", 0.0))
                ser["day"].append(d + 1)
                ser["q_gas"].append(round(float(c.get("Q_gas", 0.0)), 1))
                ser["q_ch4"].append(round(float(c.get("Q_ch4", 0.0)), 1))
                ser["ch4_pct"].append(round(float(c.get("Q_ch4", 0.0)) / float(c.get("Q_gas", 1e-9)) * 100.0, 2))
                ser["ph"].append(round(float(c.get("pH", 0.0)), 3))
                ser["vfa_tac"].append(round(vfa / tac, 4) if tac > 0 else None)
            olr, hrt = olr_hrt(self.ids, flows, self.cfg.v_liq)
            ser["olr"] = [round(olr, 2)] * len(ser["day"])
            ser["hrt"] = [round(hrt, 1)] * len(ser["day"])
            results.append(
                {
                    "label": label,
                    "flows": [round(v, 2) for v in flows],
                    "total": round(float(sum(flows)), 2),
                    "olr": round(olr, 2),
                    "hrt": round(hrt, 1),
                    "mean_ch4": _num(np.nanmean(ser["q_ch4"][-5:]), 1),
                    "min_ph": _num(np.nanmin(ser["ph"]), 2),
                    "series": ser,
                }
            )
        return {"ok": True, "days": int(days), "substrates": self.ids, "scenarios": results}

    def _safe_update(self, reading: dict[str, float], t: float) -> bool:
        """量测更新；协方差失去正定性时重建滤波器再试一次。

        连续两次在同一时刻更新（先 poll 再手工注入）会让 Cholesky 下更新失败，
        报 ``downdated matrix is not positive definite``。现场也会遇到重复读数，
        所以这里不以异常告终，而是重置先验后重来。
        """
        try:
            self._last_upd = self.ukf.update(reading, t=t, equilibration_dt=1.0 / 24.0)
            return True
        except Exception:  # noqa: BLE001 - 数值问题，不是逻辑错误
            self.ukf = None
            self._saved_x = None
            self._ensure_filter()
            try:
                self._last_upd = self.ukf.update(reading, t=t, equilibration_dt=1.0 / 24.0)
                return True
            except Exception:  # noqa: BLE001
                return False

    # -- 传感器：同一罐、同一时间线 ---------------------------------------
    def _ensure_filter(self) -> None:
        """Build the estimator once, seeded from the plant's current state."""
        if getattr(self, "ukf", None) is not None:
            return
        from pyadm1ode_estimation.estimation import InputSpec, build_ukf

        self.est = self._build(self.ids, self.flows)
        comp = self.plant.components["main_digester"]
        est_comp = self.est.components["main_digester"]
        est_comp.set_state(comp.get_state())
        self.n_state = len(comp.adm1_state)
        self.ukf = build_ukf(
            self.est,
            "main_digester",
            substrates=[
                InputSpec("first", 0, self.flows[0] if self.flows else 10.0, process_noise_std=1.5),
                InputSpec("second", 1, self.flows[1] if len(self.flows) > 1 else 5.0, process_noise_std=1.5),
            ],
            sensors=("q_gas", "q_ch4", "ph"),
        )
        if getattr(self, "_saved_x", None) is not None:
            try:
                self.ukf.x_hat[:] = np.asarray(self._saved_x, dtype=float)
            except Exception:  # noqa: BLE001 - 维度不符就退回初始先验
                pass

    def poll_sensor(self, sensor: dict[str, float] | None = None) -> dict[str, Any]:
        """Advance the plant one hour, read it through the sensor, correct the filter.

        This is the same digester the day-by-day view advances: sensors and daily
        feeding share one timeline instead of running two parallel experiments.
        """
        self._ensure_filter()
        sensor = sensor or {}
        bias = float(sensor.get("bias", 0.0))
        ph_bias = float(sensor.get("ph_bias", 0.0))
        noise = float(sensor.get("noise", 0.02))
        rng = np.random.default_rng(11 + int(self.hour))

        dt = 1.0 / 24.0
        c = self.plant.step(dt=dt)["main_digester"]
        self.t_days += dt

        truth = {"Q_gas": float(c.get("Q_gas", np.nan)), "Q_ch4": float(c.get("Q_ch4", np.nan)), "pH": float(c.get("pH", np.nan))}
        self._last_gas = truth["Q_gas"]
        reading = {
            "Q_gas": truth["Q_gas"] * (1.0 + bias) * (1.0 + noise * rng.standard_normal()),
            "Q_ch4": truth["Q_ch4"] * (1.0 + bias) * (1.0 + noise * rng.standard_normal()),
            "pH": truth["pH"] + ph_bias + noise * 0.5 * rng.standard_normal(),
        }

        self.ukf.predict(dt)
        upd = self._safe_update(reading, self.t_days)
        nis_val = float("nan")
        raw = getattr(getattr(self, "_last_upd", None), "nis", None)
        try:
            if isinstance(raw, dict):
                vals = [float(v) for v in raw.values() if np.isfinite(v)]
                nis_val = float(np.mean(vals)) if vals else float("nan")
            elif raw is not None:
                arr = np.asarray(raw, dtype=float).ravel()
                arr = arr[np.isfinite(arr)]
                nis_val = float(np.mean(arr)) if arr.size else float("nan")
        except Exception:  # noqa: BLE001 - NIS 只是指标
            nis_val = float("nan")

        hat = np.asarray(self.ukf.x_hat[: self.n_state], dtype=float)
        ref = np.asarray(c.get("adm1_state", self.plant.components["main_digester"].adm1_state), dtype=float)
        mask = np.abs(ref) > 1e-6
        err = float(np.median(np.abs((hat[mask] - ref[mask]) / ref[mask]))) if mask.any() else float("nan")

        self.hour += 1
        self.hourly["t"].append(round(self.t_days, 3))
        self.hourly["q_gas"].append(_num(reading["Q_gas"], 1))
        self.hourly["q_ch4"].append(_num(reading["Q_ch4"], 1))
        self.hourly["ph"].append(_num(reading["pH"], 3))
        self.hourly["truth_gas"].append(_num(truth["Q_gas"], 1))
        self.hourly["err"].append(_num(err, 4))
        self.hourly.setdefault("nis", []).append(_num(nis_val, 3))
        return self.snapshot()

    def ingest_external(self, reading: dict[str, float]) -> dict[str, Any]:
        """Apply an externally supplied reading (meter, CSV replay, manual entry).

        No plant step: the reading is treated as "measured now", which is what a
        real device or an operator copying a gauge provides.
        """
        self._ensure_filter()
        clean = {k: float(reading[k]) for k in ("Q_gas", "Q_ch4", "pH") if k in reading}
        if not clean:
            raise ValueError("至少要给出 Q_gas / Q_ch4 / pH 中的一个")
        self._safe_update(clean, self.t_days)

        hat = np.asarray(self.ukf.x_hat[: self.n_state], dtype=float)
        ref = np.asarray(self.plant.components["main_digester"].adm1_state, dtype=float)
        mask = np.abs(ref) > 1e-6
        err = float(np.median(np.abs((hat[mask] - ref[mask]) / ref[mask]))) if mask.any() else float("nan")

        self.hourly["t"].append(round(self.t_days, 3))
        self.hourly["q_gas"].append(_num(clean.get("Q_gas", float("nan")), 1))
        self.hourly["q_ch4"].append(_num(clean.get("Q_ch4", float("nan")), 1))
        self.hourly["ph"].append(_num(clean.get("pH", float("nan")), 3))
        self.hourly["truth_gas"].append(_num(getattr(self, "_last_gas", float("nan")), 1))
        self.hourly["err"].append(_num(err, 4))
        return self.snapshot()

    def perturb(self, factor: float = 1.3, parameter: str = "k_m_ac") -> dict[str, Any]:
        """Drift the plant's kinetic parameter ("reality changed").

        原来在 /online 页单独做，现在并进工作区会话：漂移的就是这个罐，
        所以读数、状态估计和诊断都会跟着变。
        """
        adm = self.plant.components["main_digester"].adm1
        cur = adm._kinetic.get(parameter)
        before = float(np.ravel(cur)[0]) if isinstance(cur, (list, np.ndarray)) else float(cur)
        after = before * float(factor)
        if isinstance(cur, (list, np.ndarray)):
            adm._kinetic[parameter] = np.full_like(np.asarray(cur, dtype=float), after)
        else:
            adm._kinetic[parameter] = after
        self.drifts.append({"t_days": round(self.t_days, 3), "param": parameter,
                            "before": round(before, 4), "after": round(after, 4)})
        return self.snapshot()

    def go_to_day(self, target: int) -> dict[str, Any]:
        """Rewind to day ``target`` by replaying the recorded feeds from the initial state.

        只存"当前状态"是回不去的——ADM1 是 41 维动力学系统，得从同一个初值
        按当时的投料序列重放。好处是不用每天存一份状态（那样文件会涨得很快）。
        """
        target = max(0, int(target))
        total = len(self.history)
        if target > total:
            raise ValueError(f"最多只能跳到第 {total} 天")
        ids = list(self.cfg.substrate_ids)
        flows0 = list(self.cfg.flows)
        fresh = self._build(ids, flows0)
        comp = fresh.components["main_digester"]
        if self.initial_state:
            comp.set_state(self.initial_state)

        self.plant = fresh
        self.ids, self.flows = ids, flows0
        self.series = {k: [] for k in self.series}
        self.t_days = 0.0
        self.day = 0
        self.hour = 0
        self.hourly = {k: [] for k in self.hourly}
        self.ukf = None
        self.est = None
        self.last_reco = None
        self.drifts = []

        replayed = self.history[:target]
        self.history = []
        for entry in replayed:
            self.advance(entry.get("feed") or {})
        return self.snapshot()

    # -- payload -------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Everything the daily page needs."""
        import alerts as alerts_mod

        olr, hrt = olr_hrt(self.ids, self.flows, self.cfg.v_liq)
        vfa_tac = None
        if self.series["tac"]:
            tac = self.series["tac"][-1]
            vfa = self.series["vfa"][-1]
            if tac and tac > 0:
                vfa_tac = vfa / tac
        latest = {
            "q_gas": self.series["q_gas"][-1] if self.day else None,
            "q_ch4": self.series["q_ch4"][-1] if self.day else None,
            "ch4_pct": self.series["ch4_pct"][-1] if self.day else None,
            "ph": self.series["ph"][-1] if self.day else None,
            "vfa_tac": vfa_tac,
            "olr": olr,
            "hrt": hrt,
        }
        alerts = alerts_mod.evaluate(latest, self.series) if self.day else []
        return {
            "ok": True,
            "day": self.day,
            "t_days": round(self.t_days, 3),
            "hour": self.hour,
            "hourly": self.hourly,
            "elapsed_s": round(time.perf_counter() - self.t0, 1),
            "feed": dict(zip(self.ids, self.flows, strict=False)),
            "feed_names": {sid: sub_store.zh_name(sid) for sid in self.ids},
            "series": self.series,
            "latest": latest,
            "alerts": alerts,
            "alert_summary": alerts_mod.summary(alerts),
            "recommendation": self.last_reco,
            "drifts": self.drifts[-5:],
            "config": {
                "v_liq": self.cfg.v_liq,
                "v_gas": self.cfg.v_gas,
                "t_c": self.cfg.t_c,
            },
        }
