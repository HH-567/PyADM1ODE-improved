"""参数筛选有效性检验实验（按大创计划书口径实现）

流程：
  1. 12 个候选参数，范围以代码默认值锚定 [默认/3, 默认×3]（含依据说明）
  2. 单因素扫描（基准 / 下界 / 上界）→ 范围归一化敏感度 S → 排序
  3. 拉丁超立方抽 30 组新组合（种子冻结）
  4. 每组三种设置：S_all（全部候选取抽样值，参照）、S_top（仅优先组）、
     S_ctrl（仅等数量随机对照组），后两者其余参数恢复基准
  5. 逐组绝对误差 → MAE 比较 + 配对 bootstrap 95%CI
  6. 判据：MAE 相对降低 ≥20%、CI 不含 0、有效组合 ≥80%

主指标：稳态甲烷日产率 = 150 天末段 24h 的 Q_ch4 平均（m³ CH4/d）
"""

from __future__ import annotations

import csv
import json
import time
import warnings
from pathlib import Path

import numpy as np

from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1.core import set_solver_backend_override

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]
DAYS = 150.0
DT = 1.0
STEADY_WINDOW = 24
SEED = 20261117
N_COMBOS = 30
N_BOOT = 10000
TOP_FRAC = 0.25
EFFECT_THRESHOLD = 0.20
VALID_FRACTION = 0.80

OUT = Path(__file__).with_name("results") / "out"

# 命令行：--seed N --out DIR --combos N
# 主结果用冻结种子；换种子只做稳健性检查，不作为主结果。
import sys as _sys

for _i, _a in enumerate(_sys.argv):
    if _a == "--seed" and _i + 1 < len(_sys.argv):
        SEED = int(_sys.argv[_i + 1])
    elif _a == "--out" and _i + 1 < len(_sys.argv):
        OUT = Path(_sys.argv[_i + 1])
    elif _a == "--combos" and _i + 1 < len(_sys.argv):
        N_COMBOS = int(_sys.argv[_i + 1])

# 候选参数：名字 -> (默认值, 下界, 上界, 范围依据)
# 范围依据统一为"代码默认值锚定 ×[1/3, 3]"：标定包给出的区间把 k_m_su 这类
# 阈值型参数整段压在了饱和区，端点法扫描会把它误判为无关。
CANDIDATES = {
    "k_dis_PS": (0.0473, 0.0473 / 3, 0.0473 * 3),
    "k_dis_PF": (0.473, 0.473 / 3, 0.473 * 3),
    "k_hyd_ch": (4.73, 4.73 / 3, 4.73 * 3),
    "k_hyd_pr": (4.73, 4.73 / 3, 4.73 * 3),
    "k_hyd_li": (4.73, 4.73 / 3, 4.73 * 3),
    "k_m_su": (48.6, 48.6 / 3, 48.6 * 3),
    "k_m_ac": (11.8, 11.8 / 3, 11.8 * 3),
    "k_m_h2": (56.7, 56.7 / 3, 56.7 * 3),
    "k_m_pro": (19.1, 19.1 / 3, 19.1 * 3),
    "Y_su": (0.1, 0.05, 0.15),
    "k_dec_ac": (0.0588, 0.0588 / 3, 0.0588 * 3),
    "K_S_ac": (0.15, 0.15 / 3, 0.15 * 3),
}


def build() -> BiogasPlant:
    fs = Feedstock(SUBS, feeding_freq=24, total_simtime=DAYS + 2.0)
    plant = BiogasPlant("screening")
    PlantConfigurator(plant, fs).add_digester(
        digester_id="main_digester",
        V_liq=1050.0,
        V_gas=150.0,
        T_ad=42.0 + 273.15,
        Q_substrates=FLOWS,
        k_L_a=200.0,
    )
    plant.initialize()
    return plant


def _set(adm, name: str, value: float) -> None:
    cur = adm._kinetic.get(name)
    if isinstance(cur, (list, np.ndarray)):
        adm._kinetic[name] = np.full_like(np.asarray(cur, dtype=float), float(value))
    else:
        adm._kinetic[name] = float(value)


def run_point(overrides: dict[str, float]) -> tuple[float, bool]:
    """Steady-state CH4 rate and whether the last window was flat enough."""
    plant = build()
    adm = plant.components["main_digester"].adm1
    for name, value in overrides.items():
        _set(adm, name, value)
    res = plant.simulate(duration=DAYS, dt=DT, save_interval=1.0)
    tail = [float(r["components"]["main_digester"].get("Q_ch4", np.nan)) for r in res[-STEADY_WINDOW:]]
    arr = np.asarray(tail, dtype=float)
    if not np.all(np.isfinite(arr)) or arr.size < STEADY_WINDOW:
        return float("nan"), False
    mean = float(np.mean(arr))
    drift = abs(arr[-1] - arr[0]) / max(abs(mean), 1e-9)
    return mean, drift < 0.01  # 末段变化 <1%/d


def lhs(n: int, names: list[str], rng: np.random.Generator) -> list[dict[str, float]]:
    """Latin hypercube over the parameter ranges."""
    d = len(names)
    strata = np.stack([rng.permutation(n) for _ in range(d)], axis=1)  # (n, d)
    u = (strata + rng.random((n, d))) / n
    out = []
    for i in range(n):
        row = {}
        for j, name in enumerate(names):
            _, lo, hi = CANDIDATES[name]
            row[name] = float(lo + u[i, j] * (hi - lo))
        out.append(row)
    return out


def bootstrap_ci(diffs: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    n = diffs.size
    means = np.empty(N_BOOT)
    for b in range(N_BOOT):
        means[b] = diffs[rng.integers(0, n, n)].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> int:
    warnings.filterwarnings("ignore", message=".*TAC <= 0.*")
    set_solver_backend_override("scipy")
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    # ---- 1. 基准 ---------------------------------------------------------
    base, base_ok = run_point({})
    print(f"baseline steady CH4 = {base:.2f} m3/d   steady={base_ok}")

    # ---- 2. 单因素扫描 ---------------------------------------------------
    names = list(CANDIDATES)
    sens = {}
    rows = []
    for name in names:
        default, lo, hi = CANDIDATES[name]
        rng_span = hi - lo
        y_lo, ok_lo = run_point({name: lo})
        y_hi, ok_hi = run_point({name: hi})
        d_lo = (y_lo - base) / base
        d_hi = (y_hi - base) / base
        s_lo = d_lo / ((lo - default) / rng_span) if lo != default else 0.0
        s_hi = d_hi / ((hi - default) / rng_span) if hi != default else 0.0
        s = max(abs(s_lo), abs(s_hi))
        sens[name] = s
        rows.append((name, default, lo, hi, y_lo, y_hi, d_lo * 100, d_hi * 100, s, ok_lo and ok_hi))
        print(f"  {name:10s} lo={y_lo:8.2f} hi={y_hi:8.2f}  d={d_lo*100:+7.2f}%/{d_hi*100:+7.2f}%  |S|={s:.3f}")

    ranked = sorted(names, key=lambda n: -sens[n])
    n_top = max(1, int(round(len(names) * TOP_FRAC)))
    top = ranked[:n_top]
    control_pool = ranked[n_top:]
    print(f"\npriority group ({n_top}): {top}")

    with (OUT / "sensitivity.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["param", "default", "lo", "hi", "y_lo", "y_hi", "d_lo_pct", "d_hi_pct", "abs_S", "ok"])
        w.writerows(rows)

    # ---- 3/4. LHS + 三设置 ----------------------------------------------
    rng = np.random.default_rng(SEED)
    combos = lhs(N_COMBOS, names, rng)

    records = []
    valid = 0
    for i, sample in enumerate(combos, 1):
        y_all, ok_a = run_point(sample)
        y_top, ok_t = run_point({**{k: sample[k] for k in top}})
        ctrl = control_pool[(i - 1) % len(control_pool)] if control_pool else None
        y_ctrl = float("nan")
        ok_c = False
        if ctrl is not None:
            others = [c for c in control_pool if c != ctrl]
            rng.shuffle(others)
            ctrl_set = [ctrl] + others[: n_top - 1]
            y_ctrl, ok_c = run_point({**{k: sample[k] for k in ctrl_set}})
        ok = ok_a and ok_t and ok_c
        valid += int(ok)
        e_top = abs(y_top - y_all) if ok else float("nan")
        e_ctrl = abs(y_ctrl - y_all) if ok else float("nan")
        records.append(
            {
                "combo": i,
                "y_all": y_all,
                "y_top": y_top,
                "y_ctrl": y_ctrl,
                "err_top": e_top,
                "err_ctrl": e_ctrl,
                "ctrl_set": ";".join(sorted(ctrl_set)) if ctrl_set else "",
                "valid": ok,
                **{f"x_{k}": v for k, v in sample.items()},
            }
        )
        print(f"  [{i:2d}/{N_COMBOS}] y_all={y_all:8.2f}  e_top={e_top:7.2f}  e_ctrl={e_ctrl:7.2f}  ok={ok}")

    with (OUT / "combinations.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)

    # ---- 5/6. 统计 -------------------------------------------------------
    good = [r for r in records if r["valid"]]
    err_top = np.array([r["err_top"] for r in good])
    err_ctrl = np.array([r["err_ctrl"] for r in good])
    mae_top = float(err_top.mean())
    mae_ctrl = float(err_ctrl.mean())
    reduction = (mae_ctrl - mae_top) / mae_ctrl if mae_ctrl > 0 else 0.0
    diffs = err_ctrl - err_top  # positive = priority group better
    lo_ci, hi_ci = bootstrap_ci(diffs, np.random.default_rng(SEED))
    valid_frac = valid / len(records)

    crit1 = reduction >= EFFECT_THRESHOLD
    crit2 = lo_ci > 0
    crit3 = valid_frac >= VALID_FRACTION

    summary = {
        "baseline_ch4": round(base, 3),
        "n_candidates": len(names),
        "priority": top,
        "n_combos": N_COMBOS,
        "n_valid": valid,
        "valid_fraction": round(valid_frac, 4),
        "mae_top": round(mae_top, 3),
        "mae_ctrl": round(mae_ctrl, 3),
        "mae_reduction": round(reduction, 4),
        "bootstrap_ci95": [round(lo_ci, 3), round(hi_ci, 3)],
        "criteria": {
            "reduction_ge_20pct": bool(crit1),
            "ci_excludes_zero": bool(crit2),
            "valid_ge_80pct": bool(crit3),
        },
        "verdict": "筛选有效" if (crit1 and crit2 and crit3) else "筛选未通过",
        "seed": SEED,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "runs": len(names) * 2 + 1 + N_COMBOS * 3,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n================ 结果 ================")
    print(f"有效组合      {valid}/{N_COMBOS} = {valid_frac:.0%}")
    print(f"MAE 优先组    {mae_top:.2f} m3/d")
    print(f"MAE 对照组    {mae_ctrl:.2f} m3/d")
    print(f"相对降低      {reduction:.1%}   (判据 ≥20%)")
    print(f"配对 bootstrap 95%CI [{lo_ci:+.2f}, {hi_ci:+.2f}]  (判据 不含 0)")
    print(f"判定          {'筛选有效' if (crit1 and crit2 and crit3) else '筛选未通过'}")
    print(f"总耗时        {summary['elapsed_s']}s / {summary['runs']} 次仿真")
    print(f"输出          {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
