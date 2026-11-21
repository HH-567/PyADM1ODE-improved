"""DEMO: 用 PyADM1ODE_estimation 的 UKF，从产气/pH 观测里反推进料量。

场景（孪生实验）：
  * 真值装置与滤波器装置都先预热到稳态；
  * 滤波器对进料量的先验是错的（只有真值的 60%）；
  * 只喂 Q_gas / Q_ch4 / pH（带噪声），跑 16 小时，看进料量被拉回来多少。

注意运行成本：每个小时都要跑一次 43 状态 → 87 个 sigma 点的预测，
每次预测都要推进一遍机理模型。本例 16 小时约 1 分钟。想要更好的收敛，
加长窗口或调大 InputSpec 的 process_noise_std。

运行：PyADM1ODE\\.venv\\Scripts\\python.exe demo_estimation.py
"""

import numpy as np
from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1ode_estimation.estimation import InputSpec, build_ukf

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]
N = 16  # 估计窗口（小时）
DT = 1.0 / 24.0
WARMUP_DAYS = 3.0
PRIOR_SCALE = 0.6  # 进料先验只给真值的 60%


def build() -> BiogasPlant:
    fs = Feedstock(SUBS, feeding_freq=24, total_simtime=WARMUP_DAYS + N * DT + 2.0)
    plant = BiogasPlant("est-demo")
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


def state_of(plant: BiogasPlant) -> np.ndarray:
    return np.asarray(plant.components["main_digester"].adm1_state, dtype=float)


def main() -> int:
    rng = np.random.default_rng(7)

    # 1) 两个装置用完全相同的初始状态预热到稳态
    truth = build()
    est_plant = build()
    truth.simulate(duration=WARMUP_DAYS, dt=DT, save_interval=1.0)
    est_plant.simulate(duration=WARMUP_DAYS, dt=DT, save_interval=1.0)

    # 2) 在同一状态上建滤波器，只把进料先验故意设错
    ukf = build_ukf(
        est_plant,
        "main_digester",
        substrates=[
            InputSpec("maize", 0, FLOWS[0] * PRIOR_SCALE, process_noise_std=1.5),
            InputSpec("swine", 1, FLOWS[1] * PRIOR_SCALE, process_noise_std=1.5),
        ],
        sensors=("q_gas", "q_ch4", "ph"),
    )
    n_state = 41
    i0, i1 = n_state, n_state + 1

    aligned = np.allclose(
        ukf.x_hat[:n_state], state_of(est_plant), rtol=1e-6, atol=1e-9
    )
    print(f"滤波器   : 41 个 ADM1 状态 + 2 个进料通道（共 {len(ukf.x_hat)}）")
    print(f"观测     : {[c.name for c in ukf.obs.channels]}")
    print(f"预热     : {WARMUP_DAYS:.0f} 天   估计窗口: {N} 小时")
    print(f"初始状态 : {'与真值一致' if aligned else '与真值不一致'}\n")

    errs = []
    for k in range(N):
        res = truth.simulate(duration=DT, dt=DT, save_interval=DT)
        comp = res[-1]["components"]["main_digester"]
        y = {
            "Q_gas": comp["Q_gas"] * (1.0 + 0.02 * rng.standard_normal()),
            "Q_ch4": comp["Q_ch4"] * (1.0 + 0.02 * rng.standard_normal()),
            "pH": comp["pH"] + 0.01 * rng.standard_normal(),
        }
        ukf.predict(DT)
        ukf.update(y, t=(k + 1) * DT, equilibration_dt=DT)

        if aligned and k >= N - 8:
            ts = state_of(truth)
            errs.append(np.abs(ukf.x_hat[:n_state] - ts) / np.maximum(np.abs(ts), 1e-9))

    print("进料量估计（先验只有真值的 60%）")
    for name, idx, true_flow in (("maize", i0, FLOWS[0]), ("swine", i1, FLOWS[1])):
        est = float(ukf.x_hat[idx])
        prior = true_flow * PRIOR_SCALE
        gain = 1 - abs(est - true_flow) / abs(prior - true_flow)
        print(
            f"  {name:6s} 真值 {true_flow:6.2f}   先验 {prior:6.2f}   "
            f"估计 {est:6.2f}   剩余误差 {abs(est - true_flow) / true_flow:5.1%}   "
            f"（消掉 {gain:4.0%} 的偏差）"
        )
    print("  ↑ 进料通道在 16 小时里基本没被拉动：消化器把进料扰动缓冲掉了，")
    print("    产气对进料的响应有滞后，短窗口内这个通道接近不可观测。")
    print("    想要它收敛：加长窗口（数十天量级），或把 process_noise_std 调大。")

    if errs:
        mat = np.vstack(errs)
        ts_all = state_of(truth)
        # 近零状态（强离子等）用相对误差没有意义，剔除后再统计
        keep = np.abs(ts_all) > 1e-4
        med = np.median(mat[:, keep], axis=0)
        idxs = np.flatnonzero(keep)
        hard = idxs[np.argsort(med)[::-1][:5]]
        print("\n状态量相对误差（最后 8 小时，逐状态取中位数）")
        print(f"  纳入统计 : {int(keep.sum())}/41 个状态（剔除 |真值|<1e-4 的近零量）")
        print(f"  中位相对误差 : {np.median(med):.2%}")
        print(
            "  最难收敛的 5 个 : "
            + ", ".join(f"#{i} {np.median(mat[:, i]):.0%}" for i in hard)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
