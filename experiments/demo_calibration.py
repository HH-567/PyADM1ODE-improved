"""DEMO: 用 PyADM1ODE_calibration 从"实测数据"里反演一个动力学参数。

思路是"先造真值、再假装不知道"：
  1. 拿一个正常的单消化器装置，把 k_hyd_ch 改成已知值（真值），跑出 Q_ch4 当作实测数据；
  2. 把参数复位成默认值，假装我们不知道它；
  3. 交给 Calibrator 从实测数据里反推；
  4. 比较 反推值 vs 真值，以及 校准前/后 与实测的误差。

⚠ 参数名必须和 pyadm1 的 ``ADM1._kinetic`` 键对上（33/41 对得上）。
   ``k_dis`` 和 ``k_dec_X_*`` 这 8 个名字**不存在**于 pyadm1 0.6.2 的 kinetic 字典里，
   写进去只会落在 ``_calibration_params``，仿真结果完全不变——看起来"校准无效"。
   对应关系：``k_dis`` → ``k_dis_PS`` / ``k_dis_PF``；``k_dec_X_su`` → ``k_dec_su``。

运行：PyADM1ODE\\.venv\\Scripts\\python.exe demo_calibration.py
"""

import numpy as np
import pandas as pd
from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1ode_calibration.calibration.core.simulator import PlantSimulator

from pyadm1ode_calibration import Calibrator, MeasurementData

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]
DAYS = 30

PARAM = "k_hyd_ch"  # 碳水水解速率，默认 4.0
TRUTH = 10.0
START = 4.0


def build() -> BiogasPlant:
    """A single-digester plant, same geometry as the web demo."""
    fs = Feedstock(SUBS, feeding_freq=24, total_simtime=DAYS + 2.0)
    plant = BiogasPlant("calib-demo")
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


def kinetic_of(plant: BiogasPlant) -> dict:
    """The dict the ODE right-hand side actually reads."""
    return plant.components["main_digester"].adm1._kinetic


def main() -> int:
    # 0) 先确认这个参数名真的会生效
    if PARAM not in kinetic_of(build()):
        raise SystemExit(f"{PARAM} 不在 ADM1._kinetic 里，改了也不会生效")

    # 1) 测量表：只放进料（Q_sub* 是包内的列名约定）
    data = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=DAYS, freq="D"),
            **{f"Q_sub_{i}": [float(FLOWS[i])] * DAYS for i in range(len(SUBS))},
        }
    )
    measurements = MeasurementData(data)

    # 2) 当真值跑一遍，产甲烷速率就是"实测"
    truth_out = PlantSimulator(build(), verbose=False).simulate_with_parameters(
        {PARAM: TRUTH}, measurements
    )
    measurements.data["Q_ch4"] = truth_out["Q_ch4"]
    obs = measurements.data["Q_ch4"].to_numpy()
    print(f"实测 Q_ch4 均值 ≈ {obs.mean():.1f} m3/d（{DAYS} 天，真值 {PARAM}={TRUTH}）")

    # 3) 校准：从默认值出发，只调这一个参数
    plant = build()
    calibrator = Calibrator(plant, verbose=False)
    result = calibrator.run_initial_calibration(
        measurements,
        [PARAM],
        method="nelder_mead",
        objectives=["Q_ch4"],
        max_iterations=30,
        sensitivity_analysis=False,
    )

    # 4) 校准前后各跟实测比一次
    sim = PlantSimulator(build(), verbose=False)
    before = sim.simulate_with_parameters({PARAM: START}, measurements)["Q_ch4"]
    after = sim.simulate_with_parameters(result.parameters, measurements)["Q_ch4"]
    rmse = lambda a: float(np.sqrt(np.mean((np.asarray(a) - obs) ** 2)))

    print()
    print(
        f"  方法      : {result.method}   success={result.success}   迭代={result.n_iterations}"
    )
    print(f"  真值      : {PARAM} = {TRUTH:.2f}")
    print(f"  初始      : {PARAM} = {START:.2f}   -> RMSE {rmse(before):8.3f} m3/d")
    print(
        f"  校准结果  : {PARAM} = {result.parameters[PARAM]:.2f}   -> RMSE {rmse(after):8.3f} m3/d"
    )
    print(f"  相对误差  : {abs(result.parameters[PARAM] - TRUTH) / TRUTH:.1%}")
    print(f"  耗时      : {result.execution_time:.1f} s")
    if result.validation_metrics:
        for k, v in list(result.validation_metrics.items())[:4]:
            print(f"  {k:12s}: {v:.4f}")

    # 5) 写回模型，确认 kinetic 真的变了
    calibrator.apply_calibration(result)
    now = kinetic_of(plant)[PARAM]
    print(f"\n  apply_calibration() 之后 kinetic[{PARAM}] = {now:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
