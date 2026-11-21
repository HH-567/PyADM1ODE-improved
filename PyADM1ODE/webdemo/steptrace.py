# webdemo/trace.py
"""Record the integrator's actual steps for the computation-trace visualizer.

The point of this module is honesty: the animation replays the steps the ODE
solver really took — including the step sizes it chose — instead of resampling a
smooth curve. That is why the loop below drives the integrator by hand
(``BDF.step()``) instead of calling ``solve_ivp`` once.

The one thing this file duplicates is the per-day influent schedule from
:meth:`pyadm1.components.biological.digester.Digester.step`. That duplication is
checked, not assumed: ``test_trace_reproduces_the_digester`` in
``webdemo/test_trace.py`` runs both paths and compares the final state.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np


def _digester(plant, digester_id: str):
    digester = plant.components.get(digester_id)
    if digester is None:
        raise KeyError(f"No component {digester_id!r} in plant")
    return digester


def _step_scipy(adm, y0, t0, t1, rtol, atol, max_step, method: str = "BDF"):
    """Integrate one day with a scipy solver, keeping every internal step."""
    from scipy.integrate import BDF, LSODA

    cls = LSODA if method.upper() == "LSODA" else BDF
    solver = cls(
        adm.ADM_ODE,
        float(t0),
        np.asarray(y0, dtype=float),
        float(t1),
        rtol=rtol,
        atol=atol,
        max_step=max_step,
    )

    ts, ys, hs = [], [], []
    while solver.status == "running":
        t_prev = solver.t
        solver.step()
        if solver.status == "failed":
            raise RuntimeError(f"ADM1 integration failed: {solver.message}")
        ts.append(float(solver.t))
        ys.append(np.array(solver.y, dtype=float))
        hs.append(float(solver.t - t_prev))

    if not ts:  # zero-length span
        return [float(t1)], [np.asarray(y0, dtype=float)], [0.0]
    return ts, ys, hs


def _step_julia(adm, y0, t0, t1, rtol, atol, max_step):
    """Same, through the ADM1jl/Rodas4P backend (save_everystep)."""
    from pyadm1.core import adm1_julia

    ts, us, ok = adm1_julia.integrate_trace(adm, y0, float(t0), float(t1), rtol=rtol, atol=atol, max_step=max_step)
    if not ok:
        raise RuntimeError("ADM1jl/Rodas4P integration failed")
    ts = [float(t) for t in ts]
    ys = [np.asarray(u, dtype=float) for u in us]
    hs = [0.0] + [ts[i] - ts[i - 1] for i in range(1, len(ts))]
    return ts, ys, hs


def record(
    plant,
    digester_id: str = "main_digester",
    *,
    days: float = 150.0,
    dt: float = 1.0,
    backend: str = "scipy",
    method: str = "BDF",
    label: str | None = None,
    rtol: float = 1e-6,
    atol: float = 1e-8,
    max_step_frac: float = 0.1,
    max_points: int = 1500,
    temp_step: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Run the digester step by step and return the recorded trace.

    Args:
        plant: An initialised ``BiogasPlant``.
        digester_id: Which digester to trace.
        days: Simulated days.
        dt: Outer step [days] — the control interval, as in ``Digester.step``.
        backend: ``"scipy"`` or ``"adm1jl"``.
        rtol, atol: Integrator tolerances.
        max_step_frac: ``max_step = max_step_frac * dt``, as in ``Digester.step``.
        max_points: Cap on returned steps; longer traces are strided.
        temp_step: Optional ``(day, delta_celsius)`` — the digester temperature
            is changed once, at *day*. Without a perturbation the model sits at
            steady state for the whole run (median and 90th percentile of the
            biogas rate differ by 0.1 %), so there is nothing to watch.

    Returns:
        Columnar dict: ``t``, ``h``, ``gas``, ``ch4``, ``co2``, ``ph``, ``y``
        (flattened 41·n), ``du`` (flattened 41·n), plus metadata.
    """
    digester = _digester(plant, digester_id)
    adm = digester.adm1

    max_step = max(max_step_frac * dt, 1.0e-3)
    if backend == "adm1jl":

        def step_fn(a, y, t0, t1, rt, at, ms):
            return _step_julia(a, y, t0, t1, rt, at, ms)

    else:

        def step_fn(a, y, t0, t1, rt, at, ms):
            return _step_scipy(a, y, t0, t1, rt, at, ms, method)

    y = np.asarray(digester.adm1_state, dtype=float).copy()
    n_outer = max(1, round(days / dt))

    base_t_ad = float(adm.T_ad)
    step_day = None
    if temp_step is not None:
        step_day = round(float(temp_step[0]) / dt)
        step_delta = float(temp_step[1])

    all_t: list[float] = []
    all_h: list[float] = []
    all_y: list[np.ndarray] = []
    all_gas: list[float] = []
    all_ch4: list[float] = []
    all_co2: list[float] = []
    all_ph: list[float] = []
    all_du: list[np.ndarray] = []
    events: list[dict[str, Any]] = []

    def observe(t_value: float, state: np.ndarray) -> None:
        """Record the derived quantities *for this step*.

        This must run while the parameters in force for the step are still
        installed. ``calc_gas`` is a stiff linear overflow law
        (``q = k_p · (P_wet − P_ext)``) whose equilibrated margin is only about
        0.03 bar, so evaluating it against a state from a different temperature
        inflates the flux by two orders of magnitude. (Learned the hard way:
        it reported 373,000 m³/d for a digester running at 1,900.)
        """
        q_gas, q_ch4, q_co2, _, _ = adm.calc_gas(float(state[37]), float(state[38]), float(state[39]), float(state[40]))
        all_gas.append(float(q_gas))
        all_ch4.append(float(q_ch4))
        all_co2.append(float(q_co2))
        s_h = adm._calc_ph(
            float(state[10]),
            float(state[36]),
            float(state[35]),
            float(state[34]),
            float(state[33]),
            float(state[32]),
            float(state[31]),
            float(state[29]),
            float(state[30]),
            adm._inhib_params["K_w"],
        )
        all_ph.append(float(-np.log10(max(s_h, 1e-14))))
        all_du.append(np.asarray(adm.ADM_ODE(t_value, state), dtype=float))

    t_start = time.perf_counter()
    for day in range(n_outer):
        t0 = day * dt
        t1 = t0 + dt

        if step_day is not None and day == step_day:
            # The T_ad setter recomputes kinetics, inhibition and the gas
            # constants — a real operating change, not a display trick.
            adm.T_ad = base_t_ad + step_delta
            events.append({"t": round(t0, 4), "kind": "temp_step", "delta_c": step_delta})

        adm.create_influent(digester.Q_substrates, day)
        ts, ys, hs = step_fn(adm, y, t0, t1, rtol, atol, max_step)
        for t_i, y_i, h_i in zip(ts, ys, hs):
            all_t.append(t_i)
            all_h.append(h_i)
            all_y.append(y_i)
            observe(t_i, y_i)
        y = ys[-1]
    elapsed = time.perf_counter() - t_start

    if step_day is not None:
        adm.T_ad = base_t_ad  # leave the model as we found it

    stride = max(1, int(np.ceil(len(all_t) / max_points)))
    keep = list(range(0, len(all_t), stride))
    if keep[-1] != len(all_t) - 1:
        keep.append(len(all_t) - 1)

    y_flat: list[float] = []
    du_flat: list[float] = []
    for i in keep:
        y_flat.extend(np.round(all_y[i], 6).tolist())
        du_flat.extend(np.round(all_du[i], 8).tolist())

    return {
        "backend": backend,
        "method": method,
        "label": label or f"{backend} · {method}",
        "elapsed_s": round(elapsed, 4),
        "days": days,
        "dt": dt,
        "n_raw_steps": len(all_t),
        "n_steps": len(keep),
        "stride": stride,
        "t": [round(all_t[i], 6) for i in keep],
        "h": [round(all_h[i], 8) for i in keep],
        "gas": [round(all_gas[i], 4) for i in keep],
        "ch4": [round(all_ch4[i], 4) for i in keep],
        "co2": [round(all_co2[i], 4) for i in keep],
        "ph": [round(all_ph[i], 5) for i in keep],
        "y": y_flat,
        "du": du_flat,
        "state_size": 41,
        "events": events,
        "final": {
            "ph": round(all_ph[-1], 4),
            "gas": round(all_gas[-1], 2),
            "ch4_pct": round(all_ch4[-1] / all_gas[-1] * 100.0, 2) if all_gas[-1] > 0 else 0.0,
        },
    }
