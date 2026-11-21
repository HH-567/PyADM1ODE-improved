"""最小在线闭环验证：UKF 逐步更正 + OnlineCalibrator 触发重标定。

验证「实时更正」这条路能不能走通，并量出每步真实耗时，判断可用刷新率。
"""

import time
import warnings

import numpy as np
import pandas as pd

from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1.core import set_solver_backend_override
from pyadm1ode_calibration import MeasurementData, OnlineCalibrator
from pyadm1ode_estimation.estimation import InputSpec, build_ukf

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]
WARMUP = 30.0
HOURS = 6
DT = 1.0
NOISE = 0.02


def build(days: float) -> BiogasPlant:
    fs = Feedstock(SUBS, feeding_freq=24, total_simtime=days + 2.0)
    plant = BiogasPlant("online")
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


def main() -> int:
    warnings.filterwarnings("ignore", message=".*TAC <= 0.*")
    set_solver_backend_override("scipy")
    rng = np.random.default_rng(11)

    truth = build(WARMUP + HOURS)
    est_plant = build(WARMUP + HOURS)
    truth.simulate(duration=WARMUP, dt=DT, save_interval=1.0)
    est_plant.simulate(duration=WARMUP, dt=DT, save_interval=1.0)

    n_state = len(est_plant.components["main_digester"].adm1_state)
    ukf = build_ukf(
        est_plant,
        "main_digester",
        substrates=[
            InputSpec("maize", 0, FLOWS[0] * 0.6, process_noise_std=1.5),
            InputSpec("swine", 1, FLOWS[1] * 0.6, process_noise_std=1.5),
        ],
        sensors=("q_gas", "q_ch4", "ph"),
    )

    # ---- 1) 状态实时更正 ------------------------------------------------
    print("=== 1) 状态实时更正：每来一条量测就 predict/update ===")
    errs, costs, rows = [], [], []
    for k in range(HOURS):
        res = truth.simulate(duration=DT, dt=DT, save_interval=DT)
        comp = res[-1]["components"]["main_digester"]
        ph_obs = float(comp["pH"])
        y = {
            "Q_gas": float(comp["Q_gas"]) * (1.0 + NOISE * rng.standard_normal()),
            "Q_ch4": float(comp["Q_ch4"]) * (1.0 + NOISE * rng.standard_normal()),
            "pH": ph_obs + 0.01 * NOISE * 50 * rng.standard_normal(),
        }

        t0 = time.perf_counter()
        ukf.predict(DT)
        ukf.update(y, t=(k + 1) * DT, equilibration_dt=DT)
        costs.append(time.perf_counter() - t0)

        hat = np.asarray(ukf.x_hat[:n_state], dtype=float)
        ref = np.asarray(truth.components["main_digester"].adm1_state, dtype=float)
        mask = np.abs(ref) > 1e-6
        errs.append(float(np.median(np.abs((hat[mask] - ref[mask]) / ref[mask]))))
        print(f"  t={k+1:2d} h  中位状态误差 {errs[-1]*100:5.2f}%  单步 {costs[-1]:5.2f}s")

        rows.append({"timestamp": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=k + 1),
                     "Q_gas": y["Q_gas"], "Q_ch4": y["Q_ch4"], "pH": y["pH"]})

    # ---- 2) 参数实时更正 ------------------------------------------------
    print()
    print("=== 2) 参数实时更正：触发判据 + 滑动窗口重标定 ===")
    df = pd.DataFrame(rows)
    meas = MeasurementData(df)

    cal = OnlineCalibrator(est_plant, verbose=False)

    t0 = time.perf_counter()
    need, reason = cal.should_recalibrate(meas, objectives=["Q_ch4"])
    t_check = time.perf_counter() - t0
    print(f"  should_recalibrate -> {need}  ({reason})   检查耗时 {t_check:.3f}s")

    t_cal = float("nan")
    t0 = time.perf_counter()
    try:
        result = cal.calibrate(
            meas,
            parameters=["k_hyd_ch"],
            objectives=["Q_ch4"],
            method="nelder_mead",
            max_iterations=8,
            time_window=len(df),
            max_parameter_change=0.2,
        )
        t_cal = time.perf_counter() - t0
        changed = getattr(result, "parameters", None)
        print(f"  calibrate -> 耗时 {t_cal:.2f}s  结果 {changed}")
        cal.apply_calibration(result)
        print("  apply_calibration 已写回（单次改动受 max_parameter_change=0.2 限制）")
    except Exception as exc:  # noqa: BLE001 - 就是要看失败原因
        t_cal = time.perf_counter() - t0
        print(f"  calibrate 失败: {type(exc).__name__}: {exc}")

    print()
    print("=== 成本汇总 ===")
    print(f"  UKF 单步（1 h / 87 sigma 点）：均值 {np.mean(costs):.2f}s  最大 {np.max(costs):.2f}s")
    print(f"  触发判据：{t_check:.3f}s")
    print(f"  窗口重标定（≤8 次迭代）：{t_cal:.2f}s")
    print(f"  稳定后状态中位误差：{errs[-1]*100:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
