# tests/unit/test_core/test_adm1_julia.py
"""Parity tests for the ADM1jl/Rodas4P backend.

The Julia transcription of ``ADM_ODE`` is only trustworthy if it produces the
same numbers as the Python model. These tests compare the two right-hand sides
pointwise (and one integration step end-to-end) so the two implementations
cannot drift apart unnoticed.

Skipped automatically when juliacall / the Julia packages are unavailable.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyadm1.core.adm1 import ADM1, STATE_SIZE
from pyadm1.core.adm1_julia import PARAM_FIELDS, build_param_vector, unavailable_reason
from pyadm1.core.adm1_julia import reset as reset_julia

juliacall = pytest.importorskip("juliacall")


@pytest.fixture(scope="module")
def julia_backend():
    """Load the Julia module once for the whole module."""
    from pyadm1.core import adm1_julia

    reset_julia()
    if not adm1_julia.available():
        pytest.skip(f"Julia backend unavailable: {unavailable_reason()}")
    yield adm1_julia
    reset_julia()


def _make_adm1() -> ADM1:
    from pyadm1 import Feedstock

    feedstock = Feedstock(
        ["maize_silage_milk_ripeness", "swine_manure"],
        feeding_freq=24,
        total_simtime=160,
    )
    adm = ADM1(feedstock, V_liq=1050.0, V_gas=150.0, T_ad=315.15)
    adm.create_influent([11.4, 6.1], 0)
    return adm


def _plausible_state() -> np.ndarray:
    """A state roughly at digester operating conditions (not a steady state)."""
    state = np.full(STATE_SIZE, 0.01)
    state[0:3] = [0.05, 0.01, 0.02]  # S_su, S_aa, S_fa
    state[3:7] = [0.01, 0.01, 0.01, 0.09]  # VFA
    state[7] = 1e-5  # S_h2
    state[8] = 0.05  # S_ch4
    state[9] = 0.15  # S_co2
    state[10] = 0.35  # S_nh4
    state[11] = 1.0  # S_I
    state[12:22] = 5.0  # particulates
    state[22:29] = 3.0  # biomass
    state[29] = 0.08  # S_cation
    state[30] = 0.06  # S_anion
    state[31:35] = 0.01  # VFA ions
    state[35] = 0.12  # S_hco3
    state[36] = 0.005  # S_nh3
    state[37] = 1e-5  # p_gas_h2
    state[38] = 0.6  # p_gas_ch4
    state[39] = 0.35  # p_gas_co2
    state[40] = 1.0  # pTOTAL
    return state


def _states() -> list[np.ndarray]:
    return [
        np.full(STATE_SIZE, 0.01),
        _plausible_state(),
        _plausible_state() * 0.5,
        _plausible_state() * 2.0,
    ]


def test_param_fields_all_resolve() -> None:
    """Every name in PARAM_FIELDS must exist as a float on a live ADM1."""
    adm = _make_adm1()
    packed = build_param_vector(adm)
    # 97 parameters + 37 influent entries
    assert packed.shape == (len(PARAM_FIELDS) + 37,)
    assert np.all(np.isfinite(packed))


def test_param_vector_tracks_calibration_override() -> None:
    """k_L_a / k_p overrides must reach the packed vector, not the defaults."""
    adm = _make_adm1()
    baseline = build_param_vector(adm)
    idx = {name: i for i, name in enumerate(PARAM_FIELDS)}

    adm.set_calibration_parameters({"k_L_a": 123.0})
    assert build_param_vector(adm)[idx["k_L_a"]] == pytest.approx(123.0)
    assert build_param_vector(adm)[idx["k_p"]] == pytest.approx(baseline[idx["k_p"]])


@pytest.mark.parametrize("case", range(4))
def test_rhs_matches_python(julia_backend, case: int) -> None:
    """The Julia RHS must reproduce ADM_ODE to floating-point noise."""
    adm = _make_adm1()
    state = _states()[case]

    expected = np.asarray(adm.ADM_ODE(0.0, state), dtype=float)
    actual = julia_backend.rhs_eval(adm, state)

    assert actual.shape == (STATE_SIZE,)
    scale = np.maximum(np.abs(expected), 1.0e-3)
    np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-12)
    assert np.max(np.abs(actual - expected) / scale) < 1.0e-9


def test_q_s_loss_matches_python(julia_backend) -> None:
    """The sludge-volume loss rate is shared between both implementations."""
    adm = _make_adm1()
    state = _plausible_state()
    adm.ADM_ODE(0.0, state)  # populates _q_S_loss_last
    assert julia_backend.q_s_loss_eval(adm, state) == pytest.approx(adm._q_S_loss_last, rel=1e-12)


def test_one_step_matches_scipy(julia_backend) -> None:
    """One 1-day step must agree with scipy BDF at the same tolerances."""
    from scipy.integrate import solve_ivp

    adm = _make_adm1()
    state = _plausible_state()

    reference = solve_ivp(
        fun=adm.ADM_ODE,
        t_span=(0.0, 1.0),
        y0=state,
        method="BDF",
        rtol=1e-8,
        atol=1e-10,
    )
    assert reference.success

    actual = julia_backend.integrate_step(adm, state, 0.0, 1.0, rtol=1e-8, atol=1e-10, max_step=0.1)

    np.testing.assert_allclose(actual, reference.y[:, -1], rtol=1e-5, atol=1e-7)


def test_influent_length_is_checked(julia_backend) -> None:
    """A model without an influent must fail loudly, not integrate zeros."""
    adm = _make_adm1()
    adm._state_input = None
    # build_param_vector substitutes zeros, which ADM_ODE also does, so this is
    # allowed — but a wrong-length influent is not.
    adm._state_input = [0.0] * 10
    with pytest.raises(ValueError, match="influent"):
        build_param_vector(adm)
