"""Calibration and state-estimation jobs for the web demo.

Both run the *real* packages (``pyadm1ode_calibration`` / ``pyadm1ode_estimation``)
on synthetic-but-honest data:

* calibration gets measurements produced by a known "truth" parameter value and
  has to recover it from a deliberately wrong starting guess;
* estimation is a twin experiment — truth and filter plants are warmed to the
  same state, and the filter only sees noisy Q_gas / Q_ch4 / pH.

Neither path touches Julia: both force the scipy backend, so they never contend
with the solver thread's Julia runtime.
"""

from __future__ import annotations

import time
import warnings
from typing import Any

import numpy as np
import pandas as pd
from pyadm1ode_calibration import Calibrator, MeasurementData
from pyadm1ode_calibration.calibration.core.simulator import PlantSimulator

from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1.core import set_solver_backend_override

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]

#: Parameters that really move the model. ``k_dis`` and ``k_dec_X_*`` exist in
#: the calibration package's bounds but NOT in ``ADM1._kinetic``, so writing them
#: changes nothing — they are excluded from the UI on purpose.
CALIBRATABLE = {
    "k_hyd_ch": {"zh": "碳水水解速率", "default": 4.0, "lo": 1.0, "hi": 15.0, "unit": "1/d"},
    "k_hyd_pr": {"zh": "蛋白水解速率", "default": 3.0, "lo": 1.0, "hi": 15.0, "unit": "1/d"},
    "k_hyd_li": {"zh": "脂类水解速率", "default": 1.0, "lo": 0.1, "hi": 10.0, "unit": "1/d"},
    "k_m_su": {"zh": "糖 uptake 速率", "default": 30.0, "lo": 20.0, "hi": 40.0, "unit": "1/d"},
    "k_m_ac": {"zh": "乙酸 uptake 速率", "default": 8.0, "lo": 4.0, "hi": 20.0, "unit": "1/d"},
    "k_dec_su": {"zh": "糖菌衰减速率", "default": 0.02, "lo": 0.005, "hi": 0.1, "unit": "1/d"},
    "Y_su": {"zh": "糖菌产率", "default": 0.1, "lo": 0.05, "hi": 0.15, "unit": "kgCOD/kgCOD"},
}


def _build(days: float, dt: float = 1.0) -> BiogasPlant:
    fs = Feedstock(SUBS, feeding_freq=24, total_simtime=days + 2.0)
    plant = BiogasPlant("web-lab")
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


def _kinetic(plant: BiogasPlant) -> dict:
    return plant.components["main_digester"].adm1._kinetic


def _clean(a) -> list:
    out = []
    for v in np.asarray(a, dtype=float).ravel():
        out.append(round(float(v), 6) if np.isfinite(v) else None)
    return out


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def calibrate(
    parameter: str = "k_hyd_ch",
    truth: float | None = None,
    start: float | None = None,
    days: int = 30,
    method: str = "nelder_mead",
    max_iterations: int = 30,
) -> dict[str, Any]:
    """Recover ``parameter`` from measurements generated with a known value."""
    spec = CALIBRATABLE.get(parameter)
    if spec is None:
        raise ValueError(f"不可标定的参数：{parameter}（可选：{', '.join(CALIBRATABLE)}）")
    if parameter not in _kinetic(_build(2.0)):
        raise ValueError(f"{parameter} 不在 ADM1._kinetic 里，改了不会生效")

    truth_value = float(truth if truth is not None else spec["lo"] + 0.6 * (spec["hi"] - spec["lo"]))
    start_value = float(start if start is not None else spec["default"])
    if not (spec["lo"] <= truth_value <= spec["hi"]):
        raise ValueError(f"真值需在 [{spec['lo']}, {spec['hi']}] 内")

    days = int(max(5, min(90, days)))
    t0 = time.perf_counter()
    set_solver_backend_override("scipy")
    warnings.filterwarnings("ignore", message=".*TAC <= 0.*")

    # 1) 实测数据：用真值跑一遍
    data = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=days, freq="D"),
            **{f"Q_sub_{i}": [float(FLOWS[i])] * days for i in range(len(SUBS))},
        }
    )
    measurements = MeasurementData(data)
    truth_out = PlantSimulator(_build(days), verbose=False).simulate_with_parameters({parameter: truth_value}, measurements)
    obs = np.asarray(truth_out["Q_ch4"], dtype=float)
    measurements.data["Q_ch4"] = obs

    # 2) 校准
    plant = _build(days)
    calibrator = Calibrator(plant, verbose=False)
    result = calibrator.run_initial_calibration(
        measurements,
        [parameter],
        method=method,
        objectives=["Q_ch4"],
        max_iterations=int(max(5, min(200, max_iterations))),
        sensitivity_analysis=False,
    )

    # 3) 校准前后各比一次
    sim = PlantSimulator(_build(days), verbose=False)
    before = np.asarray(sim.simulate_with_parameters({parameter: start_value}, measurements)["Q_ch4"])
    after = np.asarray(sim.simulate_with_parameters(result.parameters, measurements)["Q_ch4"])

    def metrics(a: np.ndarray) -> dict:
        rmse = float(np.sqrt(np.mean((a - obs) ** 2)))
        ss_res = float(np.sum((a - obs) ** 2))
        ss_tot = float(np.sum((obs - obs.mean()) ** 2)) or 1.0
        return {"rmse": round(rmse, 4), "r2": round(1.0 - ss_res / ss_tot, 5)}

    return {
        "ok": True,
        "parameter": parameter,
        "label": spec["zh"],
        "unit": spec["unit"],
        "bounds": [spec["lo"], spec["hi"]],
        "truth": round(truth_value, 4),
        "start": round(start_value, 4),
        "calibrated": round(float(result.parameters[parameter]), 4),
        "rel_error": round(abs(float(result.parameters[parameter]) - truth_value) / truth_value, 6),
        "method": result.method,
        "success": bool(result.success),
        "iterations": int(result.n_iterations),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "before": {**metrics(before), "value": round(start_value, 4)},
        "after": {**metrics(after), "value": round(float(result.parameters[parameter]), 4)},
        "series": {
            "t": list(range(1, days + 1)),
            "observed": _clean(obs),
            "before": _clean(before),
            "after": _clean(after),
        },
    }


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


def estimate(
    hours: int = 12,
    prior_scale: float = 0.6,
    warmup_days: float = 2.0,
    noise: float = 0.02,
) -> dict[str, Any]:
    """Twin experiment: a UKF watches a plant through noisy Q_gas/Q_ch4/pH."""
    from pyadm1ode_estimation.estimation import InputSpec, build_ukf

    hours = int(max(4, min(24, hours)))
    warmup_days = float(max(1.0, min(5.0, warmup_days)))
    dt = 1.0 / 24.0
    n_state = 41

    t0 = time.perf_counter()
    set_solver_backend_override("scipy")
    warnings.filterwarnings("ignore", message=".*TAC <= 0.*")

    rng = np.random.default_rng(7)
    truth = _build(warmup_days + hours * dt)
    est_plant = _build(warmup_days + hours * dt)
    truth.simulate(duration=warmup_days, dt=dt, save_interval=1.0)
    est_plant.simulate(duration=warmup_days, dt=dt, save_interval=1.0)

    ukf = build_ukf(
        est_plant,
        "main_digester",
        substrates=[
            InputSpec("maize", 0, FLOWS[0] * prior_scale, process_noise_std=1.5),
            InputSpec("swine", 1, FLOWS[1] * prior_scale, process_noise_std=1.5),
        ],
        sensors=("q_gas", "q_ch4", "ph"),
    )
    aligned = bool(np.allclose(ukf.x_hat[:n_state], np.asarray(est_plant.components["main_digester"].adm1_state, dtype=float)))

    i0, i1 = n_state, n_state + 1
    f0 = [float(ukf.x_hat[i0])]
    f1 = [float(ukf.x_hat[i1])]
    nis: list[float] = []
    ph_pred: list[float] = []
    ph_true: list[float] = []
    truth_states: list[list[float]] = []

    for k in range(hours):
        res = truth.simulate(duration=dt, dt=dt, save_interval=dt)
        comp = res[-1]["components"]["main_digester"]
        ph_obs = float(comp["pH"])
        y = {
            "Q_gas": float(comp["Q_gas"]) * (1.0 + noise * rng.standard_normal()),
            "Q_ch4": float(comp["Q_ch4"]) * (1.0 + noise * rng.standard_normal()),
            "pH": ph_obs + 0.01 * noise * 50 * rng.standard_normal(),
        }
        ukf.predict(dt)
        step = ukf.update(y, t=(k + 1) * dt, equilibration_dt=dt)

        f0.append(float(ukf.x_hat[i0]))
        f1.append(float(ukf.x_hat[i1]))
        ph_pred.append(float(step.y_pred.get("pH", float("nan"))) if isinstance(step.y_pred, dict) else float("nan"))
        ph_true.append(ph_obs)
        nis_v = step.nis
        if isinstance(nis_v, dict):
            nis.append(float(np.mean([v for v in nis_v.values() if np.isfinite(v)] or [0.0])))
        else:
            nis.append(float(np.mean(np.asarray(nis_v, dtype=float))))
        if aligned:
            truth_states.append(np.asarray(truth.components["main_digester"].adm1_state, dtype=float).tolist())

    state_err = []
    if aligned and truth_states:
        mat = np.abs(np.array([ukf.x_hat[:n_state]] * 1)[0] - np.asarray(truth_states[-1]))
        rel = mat / np.maximum(np.abs(np.asarray(truth_states[-1])), 1e-4)
        state_err = [round(float(v), 6) for v in rel]

    return {
        "ok": True,
        "hours": hours,
        "steps": hours,
        "aligned": aligned,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "prior_scale": prior_scale,
        "true_flow": [FLOWS[0], FLOWS[1]],
        "prior": [round(FLOWS[0] * prior_scale, 3), round(FLOWS[1] * prior_scale, 3)],
        "est_flow": [round(float(ukf.x_hat[i0]), 4), round(float(ukf.x_hat[i1]), 4)],
        "flow_series": {"t": list(range(hours + 1)), "maize": [round(v, 4) for v in f0], "swine": [round(v, 4) for v in f1]},
        "ph_series": {"t": list(range(1, hours + 1)), "pred": _clean(ph_pred), "obs": _clean(ph_true)},
        "nis": {"t": list(range(1, hours + 1)), "value": _clean(nis)},
        "state_error": state_err,
        "median_state_error": round(float(np.median([v for v in state_err if v < 1.0] or [0.0])), 6),
        "sensors": ["Q_gas", "Q_ch4", "pH"],
    }
