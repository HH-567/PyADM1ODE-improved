"""Pre-run the proposal's signal gate (门禁一) on the demo plant.

For each candidate parameter: baseline, lower bound, upper bound. Main metric as
the proposal defines it: steady-state methane rate = mean of the last 24 hourly
samples, after 150 days.
"""

import warnings

import numpy as np

from pyadm1 import BiogasPlant, Feedstock
from pyadm1.configurator.plant_configurator import PlantConfigurator
from pyadm1.core import set_solver_backend_override

SUBS = ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"]
FLOWS = [11.4, 6.1, 5.0]
DAYS, DT = 150.0, 1.0

# name, lower, upper (from pyadm1ode_calibration ParameterBounds)
CANDIDATES = [
    ("k_dis_PS", 0.01, 0.20),
    ("k_dis_PF", 0.10, 2.00),
    ("k_hyd_ch", 1.0, 15.0),
    ("k_hyd_pr", 1.0, 15.0),
    ("k_hyd_li", 1.0, 15.0),
    ("k_m_su", 20.0, 40.0),
    ("k_m_ac", 4.0, 12.0),
    ("k_m_h2", 25.0, 45.0),
    ("Y_su", 0.05, 0.15),
    ("k_dec_ac", 0.02, 0.08),
    ("k_m_pro", 8.0, 18.0),
    ("K_S_ac", 0.10, 0.25),
]


def build():
    fs = Feedstock(SUBS, feeding_freq=24, total_simtime=DAYS + 2.0)
    plant = BiogasPlant("sweep")
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


def steady_ch4(overrides: dict[str, float]) -> float:
    """Mean methane rate over the last 24 samples."""
    plant = build()
    adm = plant.components["main_digester"].adm1
    for name, val in overrides.items():
        cur = adm._kinetic.get(name)
        if isinstance(cur, (list, np.ndarray)):
            adm._kinetic[name] = np.full_like(np.asarray(cur, dtype=float), float(val))
        else:
            adm._kinetic[name] = float(val)
    res = plant.simulate(duration=DAYS, dt=DT, save_interval=1.0)
    series = []
    for r in res[-24:]:
        c = r["components"]["main_digester"]
        series.append(float(c.get("Q_ch4", np.nan)))
    return float(np.nanmean(series))


def main() -> int:
    warnings.filterwarnings("ignore", message=".*TAC <= 0.*")
    set_solver_backend_override("scipy")

    base = steady_ch4({})
    print(f"baseline steady CH4 = {base:.2f} m3/d")
    print()
    print(f"{'param':10s} {'def':>8s} {'lo→d%':>9s} {'hi→d%':>9s} {'|S|':>7s}")
    rows = []
    for name, lo, hi in CANDIDATES:
        plant = build()
        cur = plant.components["main_digester"].adm1._kinetic.get(name)
        default = float(np.ravel(cur)[0]) if isinstance(cur, (list, np.ndarray)) else float(cur)
        rng = hi - lo
        y_lo = steady_ch4({name: lo})
        y_hi = steady_ch4({name: hi})
        d_lo = (y_lo - base) / base * 100
        d_hi = (y_hi - base) / base * 100
        # range-normalised sensitivity, larger absolute effect wins
        s_lo = ((y_lo - base) / base) / ((lo - default) / rng) if lo != default else 0.0
        s_hi = ((y_hi - base) / base) / ((hi - default) / rng) if hi != default else 0.0
        s = max(abs(s_lo), abs(s_hi))
        rows.append((name, default, d_lo, d_hi, s))
        print(f"{name:10s} {default:8.3g} {d_lo:8.2f}% {d_hi:8.2f}% {s:7.3f}")

    rows.sort(key=lambda r: -r[4])
    print()
    print("sorted by |S|:", " > ".join(r[0] for r in rows[:5]))
    weak = [r for r in rows if max(abs(r[2]), abs(r[3])) < 1.0]
    print(f"参数中稳态变化 <1% 的有 {len(weak)}/{len(rows)}  ->  "
          f"{'门禁一触发（信号不足）' if len(weak)/len(rows) >= 0.7 else '门禁一通过（有信号）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
