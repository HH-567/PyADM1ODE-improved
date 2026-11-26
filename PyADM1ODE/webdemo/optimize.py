"""加料推荐：在工厂可接受的操作窗口内，找使甲烷产量最大的配料方案。

约束来自沼气工程的通行操作区间（可按厂调整）：

* pH 6.8–7.6（产甲烷最适 7.0–7.4）
* VFA/TAC < 0.4（>0.3 已属预警，>0.4 视为酸化风险）
* 有机负荷 OLR 2–5 kg VS/(m³·d)（湿式中温 CSTR 的常规区间）
* 水力停留时间 HRT ≥ 20 d（保证产甲烷菌不被冲走）

这些是工程经验值，不是本项目实测，界面上也照此标注。优化器本身只做一件事：
在满足约束的前提下，把稳态甲烷产率往高处推，并把**卡住的那条约束**报出来。
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
from daily import V_GAS, V_LIQ, olr_hrt


@dataclass
class Limits:
    """Operating window a plant manager would accept."""

    ph_min: float = 6.8
    ph_max: float = 7.6
    vfa_tac_max: float = 0.4
    olr_min: float = 1.0
    olr_max: float = 5.0
    hrt_min: float = 20.0

    def as_dict(self) -> dict[str, float]:
        return {
            "pH 下限": self.ph_min,
            "pH 上限": self.ph_max,
            "VFA/TAC 上限": self.vfa_tac_max,
            "OLR 下限": self.olr_min,
            "OLR 上限": self.olr_max,
            "HRT 下限": self.hrt_min,
        }


@dataclass
class Candidate:
    """One evaluated feeding plan."""

    flows: list[float]
    q_ch4: float
    ph: float
    vfa_tac: float
    olr: float
    hrt: float
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _build(ids: list[str], flows: list[float], t_c: float) -> BiogasPlant:
    params = [sub_store.make_params(i) for i in ids]
    fs = Feedstock(params, feeding_freq=24, total_simtime=400)
    plant = BiogasPlant("opt")
    PlantConfigurator(plant, fs).add_digester(
        digester_id="main_digester",
        V_liq=V_LIQ,
        V_gas=V_GAS,
        T_ad=t_c + 273.15,
        Q_substrates=flows,
        k_L_a=200.0,
    )
    plant.initialize()
    return plant


def _measure(plant: BiogasPlant, ids: list[str], flows: list[float], days: int, limits: Limits) -> Candidate:
    """Run ``days`` days at a fixed feed and read the tail average."""
    comp = plant.components["main_digester"]
    params = [sub_store.make_params(i) for i in ids]
    fs = Feedstock(params, feeding_freq=24, total_simtime=400)
    comp.set_feedstock(fs, Q_substrates=flows, rebuild_state=False)

    ch4, ph, vfa, tac = [], [], [], []
    for _ in range(days):
        c = plant.step(dt=1.0)["main_digester"]
        ch4.append(float(c.get("Q_ch4", np.nan)))
        ph.append(float(c.get("pH", np.nan)))
        vfa.append(float(c.get("VFA", np.nan)))
        tac.append(float(c.get("TAC", np.nan)))

    tail = slice(max(0, len(ch4) - 5), None)
    q = float(np.nanmean(ch4[tail]))
    p = float(np.nanmean(ph[tail]))
    v, a = float(np.nanmean(vfa[tail])), float(np.nanmean(tac[tail]))
    ratio = v / a if a and a > 0 else float("nan")
    olr, hrt = olr_hrt(ids, flows, V_LIQ)

    bad: list[str] = []
    if not (limits.ph_min <= p <= limits.ph_max):
        bad.append(f"pH {p:.2f} 超出 {limits.ph_min}–{limits.ph_max}")
    if ratio == ratio and ratio > limits.vfa_tac_max:
        bad.append(f"VFA/TAC {ratio:.2f} > {limits.vfa_tac_max}")
    if not (limits.olr_min <= olr <= limits.olr_max):
        bad.append(f"OLR {olr:.2f} 超出 {limits.olr_min}–{limits.olr_max}")
    if hrt < limits.hrt_min:
        bad.append(f"HRT {hrt:.1f} d < {limits.hrt_min}")
    return Candidate(flows=list(flows), q_ch4=q, ph=p, vfa_tac=ratio, olr=olr, hrt=hrt, violations=bad)


def optimize(
    ids: list[str],
    current: list[float],
    days: int = 30,
    warmup: int = 20,
    t_c: float = 42.0,
    limits: Limits | None = None,
    base_state: dict[str, Any] | None = None,
    adjustable: list[bool] | None = None,
) -> dict[str, Any]:
    """Coordinate ascent over the feed vector, returning the best feasible plan.

    ``base_state`` lets a caller optimise from a plant that is already running
    (the day-by-day session) instead of warming a fresh one up.

    ``adjustable`` marks which substrates the plant is free to change. A factory
    usually cannot choose its feedstock - the supplier decides - so only the
    remaining ones are searched; the fixed ones keep their current amount.
    """
    warnings.filterwarnings("ignore", message=".*TAC <= 0.*")
    set_solver_backend_override("scipy")
    limits = limits or Limits()
    t_start = time.perf_counter()

    if not ids:
        raise ValueError("至少选择一种底物")
    if len(current) != len(ids):
        current = [10.0] * len(ids)
    free = list(adjustable) if adjustable else [True] * len(ids)
    if len(free) != len(ids):
        free = [True] * len(ids)
    if not any(free):
        raise ValueError("至少要把一种原料设为可调，否则没有可优化的量")

    plant = _build(ids, current, t_c)
    comp = plant.components["main_digester"]
    if base_state is not None:
        comp.set_state(base_state)
    else:
        # 先按当前配料跑一段，让状态离开初值，优化才有意义
        for _ in range(warmup):
            plant.step(dt=1.0)
        base_state = comp.get_state()

    evals: list[Candidate] = []

    def evaluate(flows: list[float]) -> Candidate:
        comp.set_state(base_state)
        cand = _measure(plant, ids, flows, days, limits)
        evals.append(cand)
        return cand

    start = evaluate(list(current))
    best = start

    # 坐标上升：逐个**可调**底物试几档倍数，保留可行且更优的
    factors = (0.7, 1.0, 1.35, 1.8)
    for _round in range(2):
        improved = False
        for i in range(len(ids)):
            if not free[i]:
                continue  # 原料由供货决定，不能动
            for f in factors:
                trial = list(best.flows)
                trial[i] = max(0.0, trial[i] * f)
                if abs(trial[i] - best.flows[i]) < 1e-6:
                    continue
                cand = evaluate(trial)
                if cand.ok and cand.q_ch4 > best.q_ch4 * 1.002:
                    best = cand
                    improved = True
        if not improved:
            break

    # 整体缩放：只缩可调的那几种，固定量保持不动
    adjustable_total = sum(v for v, f in zip(best.flows, free, strict=False) if f)
    if adjustable_total > 0:
        for f in (0.7, 0.85, 1.15, 1.3):
            trial = [v * f if fr else v for v, fr in zip(best.flows, free, strict=False)]
            cand = evaluate(trial)
            if cand.ok and cand.q_ch4 > best.q_ch4 * 1.002:
                best = cand

    total = float(sum(best.flows))
    gain = (best.q_ch4 - start.q_ch4) / start.q_ch4 * 100.0 if start.q_ch4 else 0.0

    binding = ""
    if not best.ok:
        binding = "；".join(best.violations[:2])
    else:
        # 哪条约束最贴近上限，就是它挡住了继续加料
        marg = {
            f"OLR 已用 {best.olr / limits.olr_max:.0%}": 1 - best.olr / limits.olr_max,
            f"HRT 已用 {limits.hrt_min / best.hrt:.0%}": best.hrt / limits.hrt_min - 1,
            f"pH {best.ph:.2f}": (best.ph - limits.ph_min) / max(limits.ph_max - limits.ph_min, 1e-9),
        }
        binding = min(marg, key=lambda k: abs(marg[k]))

    return {
        "ok": True,
        "elapsed_s": round(time.perf_counter() - t_start, 1),
        "days": days,
        "substrates": [
            {
                "id": sid,
                "name": __import__("localization").zh_name(
                    sid, getattr(sub_store.make_params(sid), "name", sid)
                ),
            }
            for sid in ids
        ],
        "current": {
            "flows": list(current),
            "total": round(float(sum(current)), 2),
            "q_ch4": round(start.q_ch4, 1),
            "ph": round(start.ph, 3),
            "vfa_tac": round(start.vfa_tac, 3),
            "olr": round(start.olr, 2),
            "hrt": round(start.hrt, 1),
            "violations": start.violations,
        },
        "recommended": {
            "flows": [round(v, 2) for v in best.flows],
            "total": round(total, 2),
            "q_ch4": round(best.q_ch4, 1),
            "ph": round(best.ph, 3),
            "vfa_tac": round(best.vfa_tac, 3),
            "olr": round(best.olr, 2),
            "hrt": round(best.hrt, 1),
            "violations": best.violations,
        },
        "gain_pct": round(gain, 1),
        "adjustable": list(free),
        "binding": binding,
        "limits": limits.as_dict(),
        "note": "约束为沼气工程通行操作区间的经验值，按本厂实际调整；结论只在模型内成立。",
        "evals": [
            {
                "flows": [round(v, 2) for v in e.flows],
                "q_ch4": round(e.q_ch4, 1),
                "ph": round(e.ph, 3),
                "vfa_tac": round(e.vfa_tac, 3),
                "olr": round(e.olr, 2),
                "hrt": round(e.hrt, 1),
                "ok": e.ok,
                "why": e.violations[:1],
            }
            for e in evals
        ],
    }
