# webdemo/system_flow.py
"""The modular plant as a graph, with three flow channels resolved per tick.

The dashboard simulates one digester; this module simulates the *component
system* — hydrolysis tank → main fermenter → gas storages → CHP → heating —
and returns the topology together with the value on every connection at every
saved tick.

Three channel kinds travel over those connections:

    material  liquid effluent between vessels        [m³/d]
    gas       biogas from digesters into storages/CHP [m³/d]
    energy    electricity and heat out of the CHP     [kW]
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Fixed layout in normalised coordinates (0..1); the frontend only scales it.
# x grows left → right with the process, y is the vertical lane.
NODES: list[dict[str, Any]] = [
    {"id": "feed", "label": "进料", "kind": "source", "x": 0.055, "y": 0.47},
    {"id": "hydrolysis", "label": "水解罐", "kind": "digester", "x": 0.225, "y": 0.47},
    {"id": "main_digester", "label": "主发酵罐", "kind": "digester", "x": 0.405, "y": 0.47},
    {"id": "hydrolysis_storage", "label": "储气 1", "kind": "storage", "x": 0.60, "y": 0.14},
    {"id": "main_digester_storage", "label": "储气 2", "kind": "storage", "x": 0.60, "y": 0.47},
    {"id": "chp_1", "label": "CHP", "kind": "chp", "x": 0.76, "y": 0.30},
    {"id": "grid", "label": "电网", "kind": "sink", "x": 0.93, "y": 0.30},
    {"id": "heating_hydro", "label": "水解加热", "kind": "heating", "x": 0.80, "y": 0.76},
    {"id": "heating_main", "label": "主罐加热", "kind": "heating", "x": 0.52, "y": 0.86},
]

#: ``bow`` bends the edge (positive = arc upwards) so connections with the same
#: source/target pair stay distinguishable.
EDGES: list[dict[str, Any]] = [
    {"id": "e_feed", "from": "feed", "to": "hydrolysis", "channel": "material", "unit": "m³/d", "bow": 0},
    {
        "id": "e_liquid",
        "from": "hydrolysis",
        "to": "main_digester",
        "channel": "material",
        "unit": "m³/d",
        "bow": 0,
    },
    {
        "id": "e_gas1",
        "from": "hydrolysis",
        "to": "hydrolysis_storage",
        "channel": "gas",
        "unit": "m³/d",
        "bow": 0.14,
    },
    {
        "id": "e_gas2",
        "from": "main_digester",
        "to": "main_digester_storage",
        "channel": "gas",
        "unit": "m³/d",
        "bow": -0.10,
    },
    {
        "id": "e_gas3",
        "from": "hydrolysis_storage",
        "to": "chp_1",
        "channel": "gas",
        "unit": "m³/d",
        "bow": 0.06,
    },
    {
        "id": "e_gas4",
        "from": "main_digester_storage",
        "to": "chp_1",
        "channel": "gas",
        "unit": "m³/d",
        "bow": -0.06,
    },
    {"id": "e_el", "from": "chp_1", "to": "grid", "channel": "energy", "unit": "kW", "bow": 0},
    {
        "id": "e_heat1",
        "from": "chp_1",
        "to": "heating_hydro",
        "channel": "heat",
        "unit": "kW",
        "bow": 0.0,
    },
    {
        "id": "e_heat2",
        "from": "chp_1",
        "to": "heating_main",
        "channel": "heat",
        "unit": "kW",
        "bow": -0.12,
    },
]

CHANNELS = {
    "material": {"label": "物料", "color": "#7ee7ff"},
    "gas": {"label": "沼气", "color": "#c8e6b0"},
    "energy": {"label": "电能", "color": "#ffd98a"},
    "heat": {"label": "热能", "color": "#ff9e7a"},
}


def build():
    """Construct the two-stage plant used by the system view."""
    from pyadm1 import BiogasPlant, Feedstock
    from pyadm1.configurator.plant_configurator import PlantConfigurator

    feedstock = Feedstock(
        ["maize_silage_milk_ripeness", "swine_manure"],
        feeding_freq=24,
        total_simtime=200,
    )
    plant = BiogasPlant("Two-Stage Plant (demo)")
    cfg = PlantConfigurator(plant, feedstock)

    hydro_feed = [11.4, 6.1, 0, 0, 0, 0, 0, 0, 0, 0]
    cfg.add_digester(
        digester_id="hydrolysis",
        V_liq=500.0,
        V_gas=75.0,
        T_ad=318.15,
        name="Hydrolysis Pre-Tank",
        Q_substrates=hydro_feed,
    )
    cfg.add_digester(
        digester_id="main_digester",
        V_liq=1000.0,
        V_gas=150.0,
        T_ad=308.15,
        name="Main Fermenter",
        Q_substrates=[0.0] * 10,
    )
    cfg.add_chp(chp_id="chp_1", P_el_nom=500.0, eta_el=0.40, eta_th=0.45)
    cfg.add_heating(heating_id="heating_hydro", target_temperature=318.15)
    cfg.add_heating(heating_id="heating_main", target_temperature=308.15)

    cfg.connect("hydrolysis", "main_digester", "liquid")
    cfg.auto_connect_digester_to_chp("hydrolysis", "chp_1")
    cfg.auto_connect_digester_to_chp("main_digester", "chp_1")
    cfg.auto_connect_chp_to_heating("chp_1", "heating_hydro")
    cfg.auto_connect_chp_to_heating("chp_1", "heating_main")

    plant.initialize()
    return plant


def _series(components: list[dict], key: str) -> list[float]:
    return [float(c.get(key, 0.0) or 0.0) for c in components]


def run(*, days: float = 120.0, dt: float = 1.0) -> dict[str, Any]:
    """Simulate the plant and return graph + per-tick channel values."""
    plant = build()
    results = plant.simulate(duration=days, dt=dt, save_interval=1.0)
    if not results:
        raise ValueError("仿真没有返回任何时间点。")

    times = [float(r["time"]) for r in results]
    comp = [r["components"] for r in results]

    def col(node: str, key: str) -> list[float]:
        return _series([c[node] for c in comp], key)

    feed_total = 11.4 + 6.1
    edge_values: dict[str, list[float]] = {
        "e_feed": [feed_total] * len(times),
        "e_liquid": col("hydrolysis", "Q_out"),
        "e_gas1": col("hydrolysis", "Q_gas"),
        "e_gas2": col("main_digester", "Q_gas"),
        "e_gas3": col("hydrolysis_storage", "Q_gas_supplied_m3_per_day"),
        "e_gas4": col("main_digester_storage", "Q_gas_supplied_m3_per_day"),
        "e_el": col("chp_1", "P_el"),
        "e_heat1": col("heating_hydro", "P_th_used"),
        "e_heat2": col("heating_main", "P_th_used"),
    }

    node_values = {
        "feed": {"Q_feed": [feed_total] * len(times)},
        "hydrolysis": {
            "Q_gas": col("hydrolysis", "Q_gas"),
            "pH": col("hydrolysis", "pH"),
            "V_liq": col("hydrolysis", "V_liq"),
        },
        "main_digester": {
            "Q_gas": col("main_digester", "Q_gas"),
            "pH": col("main_digester", "pH"),
            "V_liq": col("main_digester", "V_liq"),
        },
        # The storages are capacity-limited: they fill and vent inside a tick, so
        # stored_volume is ~0. What matters for the flow picture is what they
        # deliver to the CHP.
        "hydrolysis_storage": {"Q_gas_supplied_m3_per_day": col("hydrolysis_storage", "Q_gas_supplied_m3_per_day")},
        "main_digester_storage": {"Q_gas_supplied_m3_per_day": col("main_digester_storage", "Q_gas_supplied_m3_per_day")},
        "chp_1": {
            "P_el": col("chp_1", "P_el"),
            "P_th": col("chp_1", "P_th"),
            "Q_gas_consumed": col("chp_1", "Q_gas_consumed"),
        },
        "grid": {"P_el": col("chp_1", "P_el")},
        "heating_hydro": {"Q_heat_supplied": col("heating_hydro", "Q_heat_supplied")},
        "heating_main": {"Q_heat_supplied": col("heating_main", "Q_heat_supplied")},
    }

    total_gas = float(
        np.trapezoid(np.array(col("hydrolysis", "Q_gas")) + np.array(col("main_digester", "Q_gas")), np.array(times))
    )

    return {
        "ok": True,
        "nodes": NODES,
        "edges": EDGES,
        "channels": CHANNELS,
        "t": [round(x, 4) for x in times],
        "edgeValues": {k: [round(v, 3) for v in vals] for k, vals in edge_values.items()},
        "nodeValues": {k: {kk: [round(v, 3) for v in vv] for kk, vv in d.items()} for k, d in node_values.items()},
        "summary": {
            "days": days,
            "points": len(times),
            "total_gas_nm3": round(total_gas, 1),
            "avg_p_el_kw": round(float(np.mean(node_values["chp_1"]["P_el"])), 2),
            "avg_heat_kw": round(
                float(np.mean(node_values["heating_hydro"]["Q_heat_supplied"]))
                + float(np.mean(node_values["heating_main"]["Q_heat_supplied"])),
                2,
            ),
        },
    }
