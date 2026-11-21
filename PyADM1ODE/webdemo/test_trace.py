# webdemo/test_trace.py
"""Checks for the computation-trace recorder.

``steptrace.record`` re-implements the per-day influent schedule from
``Digester.step`` (it has to: it drives the integrator by hand to see the
internal steps). This test proves that duplication stays faithful — the traced
run must land on the same state as the normal component-based simulation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEB_DIR.parent
for p in (str(REPO_ROOT), str(WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import steptrace  # noqa: E402

FEED = {
    "maize_silage_milk_ripeness": 11.4,
    "swine_manure": 6.1,
    "cattle_manure": 5.0,
}


def _build_plant():
    from pyadm1 import BiogasPlant, Feedstock
    from pyadm1.configurator.plant_configurator import PlantConfigurator

    feedstock = Feedstock(list(FEED), feeding_freq=24, total_simtime=40)
    plant = BiogasPlant("trace-test")
    configurator = PlantConfigurator(plant, feedstock)
    configurator.add_digester(
        digester_id="main_digester",
        V_liq=1050.0,
        V_gas=150.0,
        T_ad=315.15,
        Q_substrates=list(FEED.values()),
        k_L_a=200.0,
    )
    plant.initialize()
    return plant


def test_trace_reproduces_the_digester() -> None:
    """The hand-driven trace must agree with the stock component simulation."""
    days = 20.0

    reference_plant = _build_plant()
    results = reference_plant.simulate(duration=days, dt=1.0, save_interval=1.0)
    reference = results[-1]["components"]["main_digester"]

    traced_plant = _build_plant()
    trace = steptrace.record(traced_plant, days=days, dt=1.0, backend="scipy")

    assert trace["n_raw_steps"] > 100, "expected hundreds of internal steps"
    assert trace["t"][-1] == pytest.approx(days, abs=1e-9)

    assert trace["final"]["gas"] == pytest.approx(reference["Q_gas"], rel=2e-3)
    assert trace["final"]["ph"] == pytest.approx(reference["pH"], abs=2e-3)


def test_trace_records_a_step_for_every_state() -> None:
    """Payload shape must stay consistent with what the visualizer expects."""
    plant = _build_plant()
    trace = steptrace.record(plant, days=2.0, dt=1.0, backend="scipy", max_points=500)

    n = trace["n_steps"]
    assert n >= 2
    assert len(trace["y"]) == n * 41
    assert len(trace["du"]) == n * 41
    for key in ("t", "h", "gas", "ch4", "ph"):
        assert len(trace[key]) == n

    # step sizes must be the real ones, not a resampled uniform grid
    assert len({round(h, 12) for h in trace["h"]}) > 3


def test_trace_rejects_unknown_component() -> None:
    """A typo in the component id must fail loudly, not return empty data."""
    plant = _build_plant()
    with pytest.raises(KeyError):
        steptrace.record(plant, "no_such_digester", days=1.0)
