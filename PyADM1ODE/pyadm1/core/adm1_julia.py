# pyadm1/core/adm1_julia.py
"""
ADM1jl-backed integrator for the 41-state ADM1da model.

Why this exists
---------------
The stock PyADM1ODE path integrates :meth:`pyadm1.core.adm1.ADM1.ADM_ODE` with
``scipy.integrate.solve_ivp(method="BDF")``, restarted once per simulated day.
Profiling a 150-day single-digester run (0.80 s) showed where the time goes:

  * 12,015 right-hand-side evaluations at ~35 us each  -> 0.42 s
  * of which ~6,600 were spent building **finite-difference Jacobians**
  * 150 BDF re-initialisations                          -> 0.38 s

This module keeps the model (41 states, ADM1da, unchanged) and replaces the
numerical side with ADM1jl's solver stack: the right-hand side is evaluated in
Julia and integrated with ``Rodas4P`` — the Rosenbrock method ADM1jl itself
uses for ADM1.

Design notes
------------
* **The chemistry lives in exactly one place per language.** ``adm1da.jl`` is a
  transcription of ``ADM_ODE``; ``tests/unit/test_core/test_adm1_julia.py``
  compares them pointwise so they cannot drift apart unnoticed.
* **Parameters are not retyped in Julia.** :data:`PARAM_FIELDS` is the single
  authoritative field order; it is packed into one ``Float64`` vector here and
  injected into the Julia source at load time. A missing or renamed field fails
  loudly instead of silently reading a neighbouring number.
* **Opt-in.** ``PYADM1_SOLVER=adm1jl`` enables it; the default stays scipy, so
  every existing call site, test and example behaves exactly as before.

Requires ``juliacall`` plus the Julia packages installed by ``_setup_julia.py``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

JULIA_DIR = Path(__file__).resolve().parent / "julia"
JULIA_SOURCE = JULIA_DIR / "adm1da.jl"

STATE_SIZE = 41
INFLUENT_SIZE = 37

#: Authoritative parameter order. The Julia side receives this tuple at load
#: time and builds its NamedTuple from it, so the two can never disagree.
#: Every name must resolve to a float on a live ``ADM1`` instance.
PARAM_FIELDS: tuple[str, ...] = (
    # --- temperature-corrected kinetics (ADM1._kinetic) ---
    "k_dis_PS",
    "k_dis_PF",
    "k_hyd_ch",
    "k_hyd_pr",
    "k_hyd_li",
    "k_m_su",
    "k_m_aa",
    "k_m_fa",
    "k_m_c4",
    "k_m_pro",
    "k_m_ac",
    "k_m_h2",
    "K_S_su",
    "K_S_aa",
    "K_S_fa",
    "K_S_c4",
    "K_S_pro",
    "K_S_ac",
    "K_S_h2",
    "k_dec_su",
    "k_dec_aa",
    "k_dec_fa",
    "k_dec_c4",
    "k_dec_pro",
    "k_dec_ac",
    "k_dec_h2",
    "Y_su",
    "Y_aa",
    "Y_fa",
    "Y_c4",
    "Y_pro",
    "Y_ac",
    "Y_h2",
    # --- stoichiometry (ADM1._stoich) ---
    "C_su",
    "C_aa",
    "C_fa",
    "C_va",
    "C_bu",
    "C_pro",
    "C_ac",
    "C_ch4",
    "C_bac",
    "C_ch",
    "C_pr",
    "C_li",
    "C_I_s",
    "C_I_x",
    "N_bac",
    "N_aa",
    "N_I",
    "f_fa_li",
    "f_ch_bac",
    "f_pr_bac",
    "f_li_bac",
    "f_p_bac",
    "fXI_PS",
    "fXI_PF",
    "fSI_hyd",
    # --- fermentation product fractions (ADM1._fractions) ---
    "f_h2_su",
    "f_bu_su",
    "f_pro_su",
    "f_ac_su",
    "f_h2_aa",
    "f_va_aa",
    "f_bu_aa",
    "f_pro_aa",
    "f_ac_aa",
    # --- inhibition / acid-base constants (ADM1._inhib_params) ---
    "K_pH_aa",
    "K_pH_ac",
    "K_pH_h2",
    "K_w",
    "K_a_va",
    "K_a_bu",
    "K_a_pro",
    "K_a_ac",
    "K_a_co2",
    "K_a_IN",
    "k_A_B",
    "K_S_IN",
    "K_I_nh3",
    "K_I_h2_fa",
    "K_I_h2_c4",
    "K_I_h2_pro",
    "K_IH_pro",
    "K_IH_ac",
    "K_I_nh3_pro",
    "K_S_co2_h2",
    # --- reactor / gas constants and the coarse flow state ---
    "V_liq",
    "V_gas",
    "RT",
    "k_L_a",
    "k_p",
    "p_gas_h2o",
    "p_ext",
    "K_H_co2",
    "K_H_ch4",
    "K_H_h2",
    "q_ad",
    "q_out_override",
    "has_q_out_override",
)

_N_PARAM = len(PARAM_FIELDS)

# ---------------------------------------------------------------------------
# Julia module loading
# ---------------------------------------------------------------------------


class _LoadState:
    """Cache for the loaded Julia module and the reason a load failed.

    A holder object rather than module-level ``global`` reassignment: the
    loaders below only ever read or set attributes on it.
    """

    __slots__ = ("error", "module")

    def __init__(self) -> None:
        self.module: Any = None
        self.error: str | None = None


_STATE = _LoadState()


def _julia_fields_literal() -> str:
    """Render :data:`PARAM_FIELDS` as a Julia tuple of Symbols."""
    return "(" + "".join(f":{name}," for name in PARAM_FIELDS) + ")"


def _load_module():
    """Import juliacall and evaluate the Julia source into a module (cached)."""
    if _STATE.module is not None:
        return _STATE.module
    if _STATE.error is not None:
        raise RuntimeError(_STATE.error)

    try:
        from juliacall import Main as jl
    except ImportError as exc:  # pragma: no cover - environment dependent
        _STATE.error = (
            "juliacall is not installed. Install the Julia backend with:\n"
            "    pip install juliacall\n"
            "    python _setup_julia.py"
        )
        raise RuntimeError(_STATE.error) from exc

    try:
        # PyADM1Julia is a real package (pyadm1/core/julia/PyADM1Julia), not a
        # source string: only packages get native code cached, and that cache is
        # what removes the ~5.7 s JIT on every process start.
        jl.seval("using PyADM1Julia")
        module = jl.Main.PyADM1Julia

        # The field order is frozen in the package at generation time, so check
        # it still matches the authoritative Python tuple.
        julia_fields = tuple(str(f) for f in module.PFIELDS)
        if julia_fields != tuple(PARAM_FIELDS):
            raise RuntimeError(
                "PyADM1Julia.PFIELDS is out of sync with PARAM_FIELDS "
                f"({len(julia_fields)} vs {len(PARAM_FIELDS)} fields); regenerate it with\n"
                "    python pyadm1/core/julia/gen_package.py"
            )
        _STATE.module = module
    except Exception as exc:  # pragma: no cover - environment dependent
        _STATE.error = (
            f"Failed to load the Julia ADM1 module: {exc}\n" "Install the solver packages with:  python _setup_julia.py"
        )
        raise RuntimeError(_STATE.error) from exc

    logger.debug("Julia ADM1 module loaded (%s)", name())
    return _STATE.module


def available() -> bool:
    """True when the Julia backend can actually be loaded."""
    try:
        _load_module()
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False
    return True


def unavailable_reason() -> str | None:
    """Human-readable reason the backend is unavailable, or None if it works."""
    try:
        _load_module()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def name() -> str:
    """Backend identifier reported in logs."""
    return "adm1jl"


# ---------------------------------------------------------------------------
# Parameter packing
# ---------------------------------------------------------------------------


def build_param_vector(adm1) -> np.ndarray:
    """Flatten a live :class:`~pyadm1.core.adm1.ADM1` into one Float64 vector.

    The vector is ``PARAM_FIELDS`` followed by the 37 influent concentrations
    that ``ADM_ODE`` reads from ``_state_input``.

    Rebuild this at the start of every integration step: ``q_ad``,
    ``_state_input``, ``_Q_out_override`` and (with dynamic volume) ``V_liq``
    all change between steps.
    """
    values: dict[str, float] = {}

    for source in (adm1._kinetic, adm1._stoich, adm1._fractions, adm1._inhib_params):
        values.update({k: float(v) for k, v in source.items()})

    # Calibration overrides are applied to these two only (see ADM_ODE).
    calibration = adm1._calibration_params or {}
    k_L_a = calibration.get("k_L_a")
    values["k_L_a"] = float(adm1._k_L_a if k_L_a is None else k_L_a)
    k_p = calibration.get("k_p")
    values["k_p"] = float(adm1._k_p if k_p is None else k_p)

    values["V_liq"] = float(adm1.V_liq)
    values["V_gas"] = float(adm1._V_gas)
    values["RT"] = float(adm1._RT)
    values["p_gas_h2o"] = float(adm1._p_gas_h2o)
    values["p_ext"] = float(adm1._p_ext)
    # Henry constants are overwritten in place by set_calibration_parameters(),
    # so read the attributes rather than the ADMParams defaults.
    values["K_H_co2"] = float(adm1._K_H_co2)
    values["K_H_ch4"] = float(adm1._K_H_ch4)
    values["K_H_h2"] = float(adm1._K_H_h2)
    values["q_ad"] = float(np.sum(adm1._Q)) if adm1._Q is not None else 0.0

    override = adm1._Q_out_override
    values["has_q_out_override"] = 0.0 if override is None else 1.0
    values["q_out_override"] = 0.0 if override is None else float(override)

    missing = [f for f in PARAM_FIELDS if f not in values]
    if missing:
        raise KeyError(f"ADM1 object is missing parameters: {missing}")

    influent = adm1._state_input if adm1._state_input is not None else [0.0] * INFLUENT_SIZE
    if len(influent) != INFLUENT_SIZE:
        raise ValueError(
            f"ADM1 influent has {len(influent)} entries, expected {INFLUENT_SIZE}. "
            "Call create_influent() before integrating."
        )

    packed = [values[f] for f in PARAM_FIELDS] + [float(v) for v in influent]
    return np.ascontiguousarray(packed, dtype=np.float64)


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def integrate_step(
    adm1,
    y0,
    t0: float,
    t1: float,
    *,
    rtol: float = 1e-6,
    atol: float = 1e-8,
    max_step: float | None = None,
) -> np.ndarray:
    """Integrate one step with Rodas4P and return the final state.

    Args:
        adm1: Model instance supplying the parameters and influent.
        y0: Initial 41-element state.
        t0: Start time [days].
        t1: End time [days].
        rtol: Relative tolerance (same meaning as scipy).
        atol: Absolute tolerance (same meaning as scipy).
        max_step: Upper bound on the internal step size [days]. ``None``
            leaves it to the adaptive controller.

    Returns:
        Final state as a 41-element ``float64`` array.

    Raises:
        RuntimeError: If the Julia solver reports a non-success return code.
    """
    module = _load_module()

    pvec = build_param_vector(adm1)
    u0 = np.ascontiguousarray(y0, dtype=np.float64).reshape(-1)
    if u0.size != STATE_SIZE:
        raise ValueError(f"State vector has {u0.size} entries, expected {STATE_SIZE}.")

    dtmax = float(max_step) if max_step is not None else float(abs(t1 - t0))
    if dtmax <= 0.0:
        dtmax = float(abs(t1 - t0)) or 1.0

    y_end, ok, _n_steps = module.integrate_step(pvec, u0, float(t0), float(t1), float(rtol), float(atol), dtmax)
    if not bool(ok):
        raise RuntimeError(f"ADM1jl/Rodas4P integration failed on ({t0}, {t1})")

    return np.asarray(y_end, dtype=np.float64).reshape(STATE_SIZE)


def integrate_trace(
    adm1,
    y0,
    t0: float,
    t1: float,
    *,
    rtol: float = 1e-6,
    atol: float = 1e-8,
    max_step: float | None = None,
) -> tuple[list[float], list[np.ndarray], bool]:
    """Integrate one span keeping **every accepted internal step**.

    Same solver and tolerance handling as :func:`integrate_step`, but returns
    the whole step sequence so callers can visualize the march through time.

    Returns:
        ``(times, states, success)`` — times as floats, states as 41-element
        ``float64`` arrays.
    """
    module = _load_module()

    pvec = build_param_vector(adm1)
    u0 = np.ascontiguousarray(y0, dtype=np.float64).reshape(-1)
    if u0.size != STATE_SIZE:
        raise ValueError(f"State vector has {u0.size} entries, expected {STATE_SIZE}.")

    dtmax = float(max_step) if max_step is not None else float(abs(t1 - t0))
    if dtmax <= 0.0:
        dtmax = float(abs(t1 - t0)) or 1.0

    ts, us, ok = module.integrate_trace(pvec, u0, float(t0), float(t1), float(rtol), float(atol), dtmax)

    times = [float(t) for t in ts]
    states = [np.asarray(u, dtype=np.float64).reshape(STATE_SIZE) for u in us]
    return times, states, bool(ok)


def rhs_eval(adm1, state) -> np.ndarray:
    """Evaluate dy/dt once, used by the Python/Julia parity test."""
    module = _load_module()
    pvec = build_param_vector(adm1)
    u = np.ascontiguousarray(state, dtype=np.float64).reshape(-1)
    return np.asarray(module.rhs_eval(pvec, u), dtype=np.float64)


def q_s_loss_eval(adm1, state) -> float:
    """Sludge-volume loss rate at *state* [m³/d]."""
    module = _load_module()
    pvec = build_param_vector(adm1)
    u = np.ascontiguousarray(state, dtype=np.float64).reshape(-1)
    return float(module.q_s_loss_eval(pvec, u))


def warmup(adm1=None) -> float:
    """Compile the Julia path once; returns the elapsed seconds.

    Julia compiles per concrete function type on first call. Without a *model*
    only the right-hand side gets compiled; pass a live ``ADM1`` to also
    compile the integrator, which is what makes the first real simulation slow
    (several seconds) otherwise.
    """
    import time

    module = _load_module()
    u = np.full(STATE_SIZE, 0.01, dtype=np.float64)

    t0 = time.perf_counter()
    if adm1 is None:
        pvec = np.zeros(_N_PARAM + INFLUENT_SIZE, dtype=np.float64)
        module.rhs_eval(pvec, u)
    else:
        integrate_step(adm1, u, 0.0, 1.0e-3, rtol=1e-6, atol=1e-8, max_step=1.0e-3)
    return time.perf_counter() - t0


def is_enabled() -> bool:
    """True when ``PYADM1_SOLVER`` selects this backend."""
    return os.environ.get("PYADM1_SOLVER", "scipy").strip().lower() in {"adm1jl", "julia"}


def reset() -> None:
    """Forget the loaded module (tests only)."""
    _STATE.module = None
    _STATE.error = None
