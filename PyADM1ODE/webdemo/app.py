#!/usr/bin/env python3
"""PyADM1ODE 可视化操作平台 — FastAPI 后端。

Run from the repository root:

    .venv\\Scripts\\python.exe webdemo\\app.py
    .venv\\Scripts\\python.exe webdemo\\app.py --port 9000

Then open http://127.0.0.1:8848

The API is deliberately thin: it builds a plant from the request, runs the
ADM1da model, and returns the time series. All presentation lives in the
browser.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WEB_DIR = Path(__file__).resolve().parent
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

STATIC_DIR = WEB_DIR / "static"

import numpy as np  # noqa: E402
import steptrace  # noqa: E402
import system_flow  # noqa: E402

import pyadm1  # noqa: E402
from pyadm1 import BiogasPlant, Feedstock  # noqa: E402
from pyadm1.configurator.plant_configurator import PlantConfigurator  # noqa: E402
from pyadm1.core import adm1_julia, set_solver_backend_override  # noqa: E402

app = FastAPI(title="PyADM1ODE 可视化平台", version="1.0")

_LOG = logging.getLogger("pyadm1.webdemo")


@app.middleware("http")
async def _no_static_cache(request, call_next):
    """Serve the demo's static files with no-store.

    Without this the browser keeps running the previous engine.js after an edit
    and the fix looks like it did nothing — which has now cost time twice.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response

# ---------------------------------------------------------------------------
# One worker thread for every solver call.
#
# uvicorn runs sync endpoints in a thread pool, so two browser tabs (or a
# refresh while a request is in flight) would call into Julia concurrently.
# juliacall is an embedded runtime: concurrent calls from different Python
# threads deadlock it — the process stays alive, all threads end up waiting and
# even /api/health stops answering. Submitting every heavy job to a
# single-worker executor keeps all Julia traffic on one thread.
# ---------------------------------------------------------------------------
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="adm1")

HEAVY_TIMEOUT_S = 240


def _serial(fn, *args, **kwargs):
    """Run *fn* on the single solver thread and wait for it."""
    fut = _WORKER.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=HEAVY_TIMEOUT_S)
    except FuturesTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=f"计算超时（>{HEAVY_TIMEOUT_S}s）。首次使用 Julia 后端需编译约 10s，请稍后重试。",
        ) from exc


def _shutdown_worker() -> None:
    """Stop accepting work on exit."""
    _WORKER.shutdown(wait=False)


def _bootstrap_julia() -> None:
    """Load the Julia runtime **on the solver thread**, once.

    ``adm1_julia.available()`` imports juliacall, which starts the embedded
    Julia runtime. Doing that from a request handler initialised it on some
    uvicorn pool thread, and the first real simulation then ran on the worker
    thread and deadlocked — the process stayed alive, all threads parked, even
    /api/health stopped answering. Loading it here pins the runtime to the one
    thread that will ever use it.
    """
    try:
        if adm1_julia.available():
            # available() only loads the package; the first real call still pays
            # PythonCall's PyArray -> Vector compile (~1.5 s). Pay that here too
            # so no click ever waits for it.
            adm1_julia.warmup()
    except Exception:  # noqa: BLE001 - a missing Julia is a valid state
        _LOG.info("Julia backend unavailable at startup; compare will report it")


_WORKER.submit(_bootstrap_julia)

# atexit instead of @app.on_event: FastAPI deprecates the latter, and the
# executor must die with the process either way.
atexit.register(_shutdown_worker)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class FeedItem(BaseModel):
    """One substrate with its volumetric feed rate [m³/d]."""

    substrate_id: str
    q: float = Field(ge=0.0, le=500.0)


class SimRequest(BaseModel):
    """Everything the demo can change about the plant and the run."""

    backend: str = Field(default="scipy", pattern="^(scipy|adm1jl)$")
    duration: float = Field(default=150.0, gt=0.0, le=1000.0)
    dt: float = Field(default=1.0, gt=0.0, le=5.0)
    temperature_c: float = Field(default=42.0, ge=15.0, le=65.0)
    v_liq: float = Field(default=1050.0, gt=1.0, le=20000.0)
    v_gas: float = Field(default=150.0, gt=1.0, le=5000.0)
    k_l_a: float = Field(default=200.0, gt=0.0, le=2000.0)
    feed: list[FeedItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def substrate_ids() -> list[str]:
    """Substrate ids currently shipped in ``data/substrates``."""
    directory = REPO_ROOT / "data" / "substrates"
    return sorted(p.stem for p in directory.glob("*.yaml"))


#: Chinese names for the shipped substrates, shown after the English id as
#: ``cattle_manure（牛粪）``. Keys match the file stems in ``data/substrates``;
#: anything missing simply renders with the English id only.
SUBSTRATE_ZH: dict[str, str] = {
    "cattle_manure": "牛粪",
    "cattle_manure_solid": "牛粪 固态",
    "cereal_gps_silage": "谷物全株青贮",
    "chicken_manure_dry": "鸡粪 干",
    "corn_cob_mix": "玉米芯混合料",
    "grass_silage": "青草青贮",
    "green_rye_silage": "黑麦青贮",
    "maize_silage_gummersbach": "玉米青贮 Gummersbach",
    "maize_silage_milk_ripeness": "玉米青贮 乳熟期",
    "onion_waste": "洋葱废弃物",
    "swine_manure": "猪粪",
    "swine_manure_gummersbach": "猪粪 Gummersbach",
    "wheat_whole_plant_silage": "小麦全株青贮",
}


def substrate_labels() -> dict[str, str]:
    """Chinese label per substrate id, restricted to ids that actually exist."""
    return {sid: SUBSTRATE_ZH[sid] for sid in substrate_ids() if sid in SUBSTRATE_ZH}


def _clean(values) -> list:
    """JSON-safe list: non-finite numbers become null."""
    out = []
    for v in values:
        f = float(v)
        out.append(f if np.isfinite(f) else None)
    return out


def _col(components, key: str) -> np.ndarray:
    return np.array([c.get(key, float("nan")) for c in components], dtype=float)


def build_plant(req: SimRequest):
    """Construct and initialise the plant described by *req*."""
    feed = [(item.substrate_id, float(item.q)) for item in req.feed if item.q > 0.0]
    if not feed:
        raise ValueError("至少需要一种基质，且进料量必须大于 0。")

    ids = [f[0] for f in feed]
    flows = [f[1] for f in feed]

    feedstock = Feedstock(ids, feeding_freq=24, total_simtime=req.duration + 2.0)
    plant = BiogasPlant("web-demo")
    configurator = PlantConfigurator(plant, feedstock)
    configurator.add_digester(
        digester_id="main_digester",
        V_liq=req.v_liq,
        V_gas=req.v_gas,
        T_ad=req.temperature_c + 273.15,
        Q_substrates=flows,
        k_L_a=req.k_l_a,
    )
    plant.initialize()
    return plant, ids, flows


def run_simulation(req: SimRequest) -> dict:
    """Build the plant, integrate, and return series + summary as plain dicts."""
    feed = [(item.substrate_id, float(item.q)) for item in req.feed if item.q > 0.0]
    if not feed:
        raise ValueError("至少需要一种基质，且进料量必须大于 0。")
    if req.duration / req.dt > 4000:
        raise ValueError("时间点太多（duration/dt > 4000），请调大 dt 或缩短天数。")

    flows = [f[1] for f in feed]
    save_interval = max(req.dt, req.duration / 400.0)

    plant, _ids, _flows = build_plant(req)
    set_solver_backend_override(req.backend)
    try:
        t_start = time.perf_counter()
        results = plant.simulate(duration=req.duration, dt=req.dt, save_interval=save_interval)
        elapsed = time.perf_counter() - t_start
    finally:
        set_solver_backend_override(None)

    if not results:
        raise ValueError("仿真没有返回任何时间点。")

    times = np.array([float(r["time"]) for r in results], dtype=float)
    components = [r["components"]["main_digester"] for r in results]

    q_gas = _col(components, "Q_gas")
    q_ch4 = _col(components, "Q_ch4")
    q_co2 = _col(components, "Q_co2")
    ph = _col(components, "pH")
    vfa = _col(components, "VFA")
    tac = _col(components, "TAC")
    ts = _col(components, "TS")
    hrt = _col(components, "HRT")
    ch4_pct = np.divide(q_ch4, q_gas, out=np.zeros_like(q_gas), where=q_gas > 0) * 100.0

    feed_total = float(np.sum(flows))
    total_gas = float(np.trapezoid(q_gas, times)) if len(times) > 1 else 0.0
    total_ch4 = float(np.trapezoid(q_ch4, times)) if len(times) > 1 else 0.0

    summary = {
        "avg_q_gas": float(np.nanmean(q_gas)) if q_gas.size else 0.0,
        "avg_q_ch4": float(np.nanmean(q_ch4)) if q_ch4.size else 0.0,
        "avg_ch4_pct": float(np.nanmean(ch4_pct)) if ch4_pct.size else 0.0,
        "max_q_gas": float(np.nanmax(q_gas)) if q_gas.size else 0.0,
        "final_ph": float(ph[-1]) if ph.size else 0.0,
        "final_vfa": float(vfa[-1]) if vfa.size else 0.0,
        "final_tac": float(tac[-1]) if tac.size else 0.0,
        "final_ts": float(ts[-1]) if ts.size else 0.0,
        "final_hrt": float(hrt[-1]) if hrt.size else 0.0,
        "total_gas_nm3": total_gas,
        "total_ch4_nm3": total_ch4,
        "yield_m3_per_m3_feed": total_gas / (feed_total * req.duration) if feed_total > 0 else 0.0,
        "feed_total": feed_total,
        "points": len(times),
    }

    return {
        "ok": True,
        "backend": req.backend,
        "elapsed_s": round(elapsed, 4),
        "series": {
            "time": _clean(times),
            "q_gas": _clean(q_gas),
            "q_ch4": _clean(q_ch4),
            "q_co2": _clean(q_co2),
            "ch4_pct": _clean(ch4_pct),
            "ph": _clean(ph),
            "vfa": _clean(vfa),
            "tac": _clean(tac),
            "ts": _clean(ts),
            "hrt": _clean(hrt),
        },
        "summary": {k: round(v, 5) if isinstance(v, float) else v for k, v in summary.items()},
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    """Static information the UI needs once at start-up.

    Deliberately does *not* load Julia — that costs seconds and is only needed
    when the user actually asks for the Julia backend.
    """
    import importlib.util

    return {
        "version": pyadm1.__version__,
        "model": "ADM1da · 41 状态",
        "substrates": substrate_ids(),
        "substrate_labels": substrate_labels(),
        "julia_installed": importlib.util.find_spec("juliacall") is not None,
        "defaults": {
            "backend": "scipy",
            "duration": 150.0,
            "dt": 1.0,
            "temperature_c": 42.0,
            "v_liq": 1050.0,
            "v_gas": 150.0,
            "k_l_a": 200.0,
            "feed": [
                {"substrate_id": "maize_silage_milk_ripeness", "q": 11.4},
                {"substrate_id": "swine_manure", "q": 6.1},
                {"substrate_id": "cattle_manure", "q": 5.0},
            ],
        },
    }


def _trace_one(req: TraceRequest) -> dict:
    """Body of /api/trace, kept separate so it can run on the solver thread."""
    plant, _ids, _flows = build_plant(req)
    perturbation = None
    if req.temp_step_day is not None:
        perturbation = (float(req.temp_step_day), float(req.temp_step_delta_c))
    data = steptrace.record(
        plant,
        "main_digester",
        days=req.duration,
        dt=req.dt,
        backend=req.backend,
        max_points=req.max_points,
        temp_step=perturbation,
    )
    return {"ok": True, **data}


def _traces_all(req: TracesRequest) -> dict:
    """Body of /api/traces: every channel, on the solver thread."""
    perturbation = None
    if req.temp_step_day is not None:
        perturbation = (float(req.temp_step_day), float(req.temp_step_delta_c))

    channels = []
    for backend, method, label in TRACE_CHANNELS:
        plant, _ids, _flows = build_plant(req)  # fresh state per channel
        channels.append(
            steptrace.record(
                plant,
                "main_digester",
                days=req.duration,
                dt=req.dt,
                backend=backend,
                method=method,
                label=label,
                max_points=req.max_points,
                temp_step=perturbation,
            )
        )

    return {
        "ok": True,
        "channels": channels,
        "events": channels[0].get("events", []),
        "days": req.duration,
    }


def _compare_runs(req: SimRequest) -> tuple[dict, dict]:
    """Body of /api/compare: warm up, then time both solvers.

    The availability check lives here on purpose: it loads Julia, and that must
    happen on the solver thread (see :func:`_bootstrap_julia`).
    """
    if not adm1_julia.available():
        raise ValueError(f"Julia 后端不可用：{adm1_julia.unavailable_reason()}")
    _warmup_julia(req)
    scipy_run = run_simulation(req.model_copy(update={"backend": "scipy"}))
    julia_run = run_simulation(req.model_copy(update={"backend": "adm1jl"}))
    return scipy_run, julia_run


@app.post("/api/simulate")
def simulate(req: SimRequest) -> dict:
    """Run one simulation and return its time series."""
    try:
        return _serial(run_simulation, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


class TraceRequest(SimRequest):
    """A simulation plus how many steps the trace may return."""

    max_points: int = Field(default=1500, ge=100, le=6000)
    temp_step_day: float | None = Field(default=None, ge=0.0, le=1000.0)
    temp_step_delta_c: float = Field(default=-5.0, ge=-20.0, le=20.0)


@app.post("/api/trace")
def trace_steps(req: TraceRequest) -> dict:
    """Record every internal step the integrator takes, for the trace view."""
    if req.duration / req.dt > 4000:
        raise HTTPException(status_code=400, detail="时间点太多（duration/dt > 4000），请调大 dt。")
    try:
        return _serial(_trace_one, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/trace")
def trace_page() -> FileResponse:
    """Serve the full-screen computation-trace visualizer."""
    return FileResponse(STATIC_DIR / "trace.html")


class TracesRequest(TraceRequest):
    """Three solver channels for the same scenario."""

    max_points: int = Field(default=900, ge=100, le=3000)


#: The three channels compared side by side. torch is deliberately absent: it is
#: an alternative *derivative* backend, not a separate stepper, so it would take
#: the same BDF steps and add 2.5 GB for a near-identical third ring.
TRACE_CHANNELS: list[tuple[str, str, str]] = [
    ("scipy", "BDF", "scipy · BDF"),
    ("scipy", "LSODA", "scipy · LSODA"),
    ("adm1jl", "Rodas4P", "ADM1jl · Rodas4P"),
]


@app.post("/api/traces")
def trace_channels(req: TracesRequest) -> dict:
    """Record one step trace per solver channel, for the same plant."""
    if req.duration / req.dt > 4000:
        raise HTTPException(status_code=400, detail="时间点太多（duration/dt > 4000），请调大 dt。")

    try:
        return _serial(_traces_all, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/system")
def system_page() -> FileResponse:
    """Serve the component-system view."""
    return FileResponse(STATIC_DIR / "system.html")


@app.get("/engine")
def engine_page() -> FileResponse:
    """Serve the solver-engine view (original renderer, real step trace)."""
    return FileResponse(STATIC_DIR / "engine.html")


@app.get("/api/system")
def system_data(days: float = 120.0) -> dict:
    """Simulate the modular two-stage plant and return graph + channel flows."""
    if days < 5.0 or days > 400.0:
        raise HTTPException(status_code=400, detail="天数需在 5–400 之间。")
    try:
        return _serial(system_flow.run, days=days, dt=1.0)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


def _warmup_julia(req: SimRequest) -> None:
    """Compile the Julia integration path before it is timed.

    Julia compiles on the first call (~6 s). Paying that here keeps the
    comparison honest: the reported speed-up is the steady-state one, and the
    user is told in the UI that the first comparison takes a while.
    """
    from pyadm1.core.adm1 import ADM1

    feed = [(item.substrate_id, float(item.q)) for item in req.feed if item.q > 0.0]
    if not feed:
        return
    ids = [f[0] for f in feed]
    flows = [f[1] for f in feed]
    feedstock = Feedstock(ids, feeding_freq=24, total_simtime=5.0)
    model = ADM1(
        feedstock,
        V_liq=req.v_liq,
        V_gas=req.v_gas,
        T_ad=req.temperature_c + 273.15,
    )
    model.create_influent(flows, 0)
    adm1_julia.warmup(model)


@app.post("/api/compare")
def compare(req: SimRequest) -> dict:
    """Run the same plant through both solvers and report time and deviation."""
    try:
        scipy_run, julia_run = _serial(_compare_runs, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    deviations = {}
    for key in ("q_gas", "q_ch4", "ph"):
        a = np.array([v for v in scipy_run["series"][key] if v is not None], dtype=float)
        b = np.array([v for v in julia_run["series"][key] if v is not None], dtype=float)
        n = min(a.size, b.size)
        if n == 0:
            deviations[key] = None
            continue
        denom = np.maximum(np.abs(a[:n]), 1e-9)
        deviations[key] = float(np.max(np.abs(a[:n] - b[:n]) / denom))

    return {
        "ok": True,
        "scipy": {"elapsed_s": scipy_run["elapsed_s"], "summary": scipy_run["summary"]},
        "adm1jl": {"elapsed_s": julia_run["elapsed_s"], "summary": julia_run["summary"]},
        "speedup": round(scipy_run["elapsed_s"] / max(julia_run["elapsed_s"], 1e-9), 2),
        "max_relative_deviation": deviations,
    }


# ---------------------------------------------------------------------------
# Calibration / estimation lab
# ---------------------------------------------------------------------------


class CalibrationRequest(BaseModel):
    """Which parameter to recover, and from what starting guess."""

    parameter: str = "k_hyd_ch"
    truth: float | None = None
    start: float | None = None
    days: int = Field(default=30, ge=5, le=90)
    method: str = "nelder_mead"
    max_iterations: int = Field(default=30, ge=5, le=200)


class EstimationRequest(BaseModel):
    """Twin-experiment window and how wrong the feed prior is."""

    hours: int = Field(default=12, ge=4, le=24)
    prior_scale: float = Field(default=0.6, ge=0.2, le=1.4)


@app.get("/calibration")
def calibration_page() -> FileResponse:
    """Serve the parameter-calibration view."""
    return FileResponse(STATIC_DIR / "calibration.html")


@app.get("/estimation")
def estimation_page() -> FileResponse:
    """Serve the state-estimation view."""
    return FileResponse(STATIC_DIR / "estimation.html")


@app.get("/api/lab/meta")
def lab_meta() -> dict:
    """Which parameters the calibration view may offer."""
    try:
        import lab
    except Exception as exc:  # pragma: no cover - depends on install state
        raise HTTPException(500, f"lab 模块不可用：{exc}") from exc
    return {
        "ok": True,
        "parameters": [{"id": k, **v} for k, v in lab.CALIBRATABLE.items()],
        "flows": lab.FLOWS,
        "substrates": lab.SUBS,
    }


@app.post("/api/calibration")
def run_calibration(req: CalibrationRequest) -> dict:
    """Recover one kinetic parameter from synthetic measurements."""
    try:
        import lab

        return _serial(
            lab.calibrate,
            parameter=req.parameter,
            truth=req.truth,
            start=req.start,
            days=req.days,
            method=req.method,
            max_iterations=req.max_iterations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/estimation")
def run_estimation(req: EstimationRequest) -> dict:
    """Run the twin-experiment UKF and return the trajectory."""
    try:
        import lab

        return _serial(lab.estimate, hours=req.hours, prior_scale=req.prior_scale)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Online correction: per-measurement state update + window recalibration
# ---------------------------------------------------------------------------

_ONLINE: dict[str, Any] = {"session": None}


class OnlineStartRequest(BaseModel):
    """Opening settings for an online-correction run."""

    dt: float = Field(default=1.0, ge=0.25, le=6.0)
    warmup_days: float = Field(default=30.0, ge=10.0, le=90.0)
    prior_scale: float = Field(default=0.6, ge=0.2, le=1.4)
    noise: float = Field(default=0.02, ge=0.0, le=0.2)
    calib_every: int = Field(default=6, ge=0, le=48)
    seed: int = 7


class OnlineStepRequest(BaseModel):
    """How many measurements to ingest in this call."""

    steps: int = Field(default=1, ge=1, le=8)


class OnlinePerturbRequest(BaseModel):
    """Multiplier applied to the truth plant's parameter ("reality drifted")."""

    factor: float = Field(default=1.4, ge=1.05, le=3.0)


@app.get("/online")
def online_page() -> FileResponse:
    """Serve the online-correction view."""
    return FileResponse(STATIC_DIR / "online.html")


def _online_session():
    session = _ONLINE.get("session")
    if session is None:
        raise HTTPException(status_code=409, detail="尚未启动会话，先点「启动会话」")
    return session


@app.post("/api/online/start")
def online_start(req: OnlineStartRequest) -> dict:
    """Create a fresh session (truth plant, filter, calibrator)."""
    try:
        import online as online_mod

        old = _ONLINE.get("session")
        if old is not None:
            old.close()
        cfg = online_mod.OnlineConfig(
            dt=req.dt,
            warmup_days=req.warmup_days,
            prior_scale=req.prior_scale,
            noise=req.noise,
            calib_every=req.calib_every,
            seed=req.seed,
        )
        session = _serial(online_mod.OnlineSession, cfg)
        _ONLINE["session"] = session
        return session.snapshot()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/online/state")
def online_state() -> dict:
    """Return the live snapshot so a page refresh keeps its view."""
    session = _ONLINE.get("session")
    if session is None:
        return {"active": False}
    return {"active": True, "snapshot": session.snapshot()}


@app.post("/api/online/step")
def online_step(req: OnlineStepRequest) -> dict:
    """Ingest measurements: filter every one, recalibrate on schedule."""
    try:
        session = _online_session()
        return _serial(session.step, req.steps)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/online/perturb")
def online_perturb(req: OnlinePerturbRequest) -> dict:
    """Drift the truth plant's parameter so the next checkpoint has work to do."""
    try:
        session = _online_session()
        return _serial(session.perturb, req.factor)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/online/stop")
def online_stop() -> dict:
    """Forget the session."""
    session = _ONLINE.get("session")
    if session is not None:
        session.close()
    _ONLINE["session"] = None
    return {"ok": True}


# ---------------------------------------------------------------------------
# Plant operation: substrates / daily feeding / feed optimisation / sensors
# ---------------------------------------------------------------------------

_DAILY: dict[str, Any] = {"session": None}
_SENSORS: dict[str, Any] = {"hub": None}

import workspace as ws_mod  # noqa: E402

WS = ws_mod.WorkspaceManager()


def _ws_daily() -> Any:
    """Return the daily session, rebuilding it from the active workspace if needed."""
    session = _DAILY.get("session")
    if session is not None:
        return session
    payload = WS.load("daily")
    if not payload:
        return None
    import daily as daily_mod

    session = _serial(daily_mod.DailySession.from_payload, payload)
    _DAILY["session"] = session
    return session


def _save_daily(session: Any, snap: dict[str, Any]) -> None:
    """Persist the session into the active workspace after every mutation."""
    try:
        WS.save(
            "daily",
            session.to_payload(),
            summary={
                "day": snap.get("day"),
                "q_ch4": (snap.get("latest") or {}).get("q_ch4"),
                "feed": snap.get("feed"),
            },
        )
    except Exception:  # noqa: BLE001 - 存不下来不该让推进失败
        pass


class SubstrateRequest(BaseModel):
    """A user-defined substrate (fields missing here fall back to the template)."""

    id: str | None = None
    name: str
    based_on: str = "maize_silage_milk_ripeness"
    note: str = ""
    values: dict[str, float] = Field(default_factory=dict)


class DailyStartRequest(BaseModel):
    """Opening blend and reactor size for a day-by-day session."""

    substrate_ids: list[str] = Field(default_factory=lambda: ["maize_silage_milk_ripeness", "swine_manure", "cattle_manure"])
    flows: list[float] = Field(default_factory=lambda: [11.4, 6.1, 5.0])
    v_liq: float = Field(default=1050.0, ge=100.0, le=10000.0)
    v_gas: float = Field(default=150.0, ge=10.0, le=5000.0)
    t_c: float = Field(default=42.0, ge=20.0, le=60.0)


class DailyAdvanceRequest(BaseModel):
    """Today's feed, substrate id -> m³/d."""

    feed: dict[str, float]


class DailyAutoRequest(BaseModel):
    """Keep feeding by the recommendation for N days."""

    days: int = Field(default=7, ge=1, le=60)
    adjustable: list[str] | None = None
    reopt_every: int = Field(default=7, ge=1, le=60)
    days_per_eval: int = Field(default=30, ge=10, le=90)
    limits: dict[str, float] = Field(default_factory=dict)


class DailyGotoRequest(BaseModel):
    """Rewind the session to a given day."""

    day: int = Field(ge=0, le=2000)


class DailyCompareRequest(BaseModel):
    """Several blends to run side by side from the same state."""

    days: int = Field(default=30, ge=5, le=120)
    scenarios: list[dict[str, Any]]


class OptimizeRequest(BaseModel):
    """Which substrates to optimise over, and where to start from."""

    adjustable: list[str] | None = None
    substrate_ids: list[str]
    flows: list[float]
    days: int = Field(default=30, ge=10, le=90)
    t_c: float = Field(default=42.0, ge=20.0, le=60.0)
    limits: dict[str, float] = Field(default_factory=dict)


class SensorStartRequest(BaseModel):
    """Driver settings for the sensor page."""

    kind: str = "simulated"
    bias: float = Field(default=0.0, ge=-0.5, le=0.5)
    ph_bias: float = Field(default=0.0, ge=-1.5, le=1.5)
    dropout: float = Field(default=0.0, ge=0.0, le=0.5)
    prior_scale: float = Field(default=0.6, ge=0.2, le=1.4)


class SensorReadRequest(BaseModel):
    """A reading pushed in from outside (manual entry or a real driver)."""

    Q_gas: float | None = None
    Q_ch4: float | None = None
    pH: float | None = None


class RecommendRequest(BaseModel):
    """Which substrates the plant may change, and the operating window."""

    adjustable: list[str] | None = None
    limits: dict[str, float] = Field(default_factory=dict)


def _adjustable_map(ids: list[str] | None) -> dict[str, bool] | None:
    """Turn "these are adjustable" into ``{substrate_id: bool}``.

    ``None`` means "everything is adjustable", which is the old behaviour.
    """
    if ids is None:
        return None
    return {sid: True for sid in ids}


@app.get("/substrates")
def substrates_page() -> FileResponse:
    """Serve the substrate library."""
    return FileResponse(STATIC_DIR / "substrates.html")


@app.get("/daily")
def daily_page() -> FileResponse:
    """Serve the day-by-day feeding view."""
    return FileResponse(STATIC_DIR / "daily.html")


@app.get("/optimize")
def optimize_page() -> FileResponse:
    """Serve the feed-recommendation view."""
    return FileResponse(STATIC_DIR / "optimize.html")


# ---------------------------------------------------------------------------
# Sensors: they read the *same* digester the day-by-day view advances
# ---------------------------------------------------------------------------


class SensorConfigRequest(BaseModel):
    """How the (simulated) sensor misbehaves."""

    bias: float = Field(default=0.0, ge=-0.5, le=0.5)
    ph_bias: float = Field(default=0.0, ge=-1.5, le=1.5)
    noise: float = Field(default=0.02, ge=0.0, le=0.3)


class SensorReadRequest(BaseModel):
    """A reading pushed in from outside (meter, replay, manual entry)."""

    Q_gas: float | None = None
    Q_ch4: float | None = None
    pH: float | None = None


@app.get("/sensors")
def sensors_page() -> FileResponse:
    """Serve the sensor view (bound to the active workspace session)."""
    return FileResponse(STATIC_DIR / "sensors.html")


@app.post("/api/sensors/config")
def api_sensors_config(req: SensorConfigRequest) -> dict:
    """Store the sensor model used by the next polls."""
    _SENSORS["cfg"] = {
        "bias": req.bias,
        "ph_bias": req.ph_bias,
        "noise": req.noise,
    }
    return {"ok": True, "config": _SENSORS["cfg"]}


@app.post("/api/sensors/poll")
def api_sensors_poll() -> dict:
    """Advance the plant one hour, read it, correct the filter."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="还没有会话：先在「按天推进」启动一个工作区会话")
    try:
        snap = _serial(session.poll_sensor, _SENSORS.get("cfg") or {})
        _save_daily(session, snap)
        return snap
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/sensors/push")
def api_sensors_push(req: SensorReadRequest) -> dict:
    """Apply an externally supplied reading to the filter."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="还没有会话：先在「按天推进」启动一个工作区会话")
    reading = {k: v for k, v in req.model_dump().items() if v is not None}
    if not reading:
        raise HTTPException(status_code=400, detail="至少要给出 Q_gas / Q_ch4 / pH 中的一个")
    try:
        snap = _serial(session.ingest_external, reading)
        _save_daily(session, snap)
        return snap
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/sensors/state")
def api_sensors_state() -> dict:
    """Current session snapshot for the sensor page."""
    session = _ws_daily()
    if session is None:
        return {"ok": False, "active": False}
    snap = session.snapshot()
    snap["active"] = True
    snap["sensor_config"] = _SENSORS.get("cfg") or {}
    return snap


@app.get("/api/substrates")
def api_substrates() -> dict:
    """Built-in and user-defined substrates."""
    try:
        import substrates as sub_mod

        return {
            "ok": True,
            "items": sub_mod.list_substrates(),
            "fields": [
                {"key": k, "label": sub_mod.LABELS[k][0], "unit": sub_mod.LABELS[k][1]}
                for k in sub_mod.EDITABLE
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/substrates")
def api_substrate_save(req: SubstrateRequest) -> dict:
    """Create or update a custom substrate."""
    try:
        import substrates as sub_mod

        rec = sub_mod.save_custom(req.model_dump())
        return {"ok": True, "item": rec}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.delete("/api/substrates/{substrate_id}")
def api_substrate_delete(substrate_id: str) -> dict:
    """Remove a custom substrate."""
    try:
        import substrates as sub_mod

        return {"ok": sub_mod.delete_custom(substrate_id)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/daily/start")
def api_daily_start(req: DailyStartRequest) -> dict:
    """Open a day-by-day session."""
    try:
        import daily as daily_mod

        if not WS.active_id:
            # 没有工作区就自动建一个：不该逼用户先去别的页面选
            import time as _time

            rec = WS.create(_time.strftime("工作区 %m-%d %H:%M"))
            WS.open(rec["id"])
        old = _DAILY.get("session")
        if old is not None:
            _DAILY["session"] = None
        cfg = daily_mod.DailyConfig(
            substrate_ids=list(req.substrate_ids),
            flows=list(req.flows),
            v_liq=req.v_liq,
            v_gas=req.v_gas,
            t_c=req.t_c,
        )
        session = _serial(daily_mod.DailySession, cfg)
        _DAILY["session"] = session
        snap = session.snapshot()
        _save_daily(session, snap)
        return snap
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/daily/advance")
def api_daily_advance(req: DailyAdvanceRequest) -> dict:
    """Feed today's blend and step one day."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="会话未启动")
    try:
        snap = _serial(session.advance, req.feed)
        _save_daily(session, snap)
        return snap
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/daily/state")
def api_daily_state() -> dict:
    """Current day-by-day snapshot (for refresh)."""
    session = _ws_daily()
    return session.snapshot() if session is not None else {"ok": False, "active": False}


@app.post("/api/daily/recommend")
def api_daily_recommend(req: RecommendRequest | None = None) -> dict:
    """Recommendation computed from the session's current state."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="会话未启动")
    try:
        adjustable = _adjustable_map(req.adjustable if req else None)
        rec = _serial(session.recommend, limits=(req.limits if req else None), adjustable=adjustable)
        session.last_reco = {
            "day": session.day,
            "ids": list(session.ids),
            "names": [rec["substrates"][i]["name"] for i in range(len(session.ids))],
            "current": list(rec["current"]["flows"]),
            "recommended": list(rec["recommended"]["flows"]),
            "q_ch4_now": rec["current"]["q_ch4"],
            "q_ch4_rec": rec["recommended"]["q_ch4"],
            "gain_pct": rec["gain_pct"],
            "binding": rec["binding"],
            "violations": rec["recommended"]["violations"],
        }
        _save_daily(session, session.snapshot())
        return rec
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/daily/auto")
def api_daily_auto(req: DailyAutoRequest) -> dict:
    """Advance several days, re-recommending the blend on schedule."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="会话未启动")
    try:
        snap = _serial(
            session.auto_recommended,
            days=req.days,
            reopt_every=req.reopt_every,
            days_per_eval=req.days_per_eval,
            limits=req.limits,
            adjustable=_adjustable_map(req.adjustable),
        )
        _save_daily(session, snap)
        return snap
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/daily/goto")
def api_daily_goto(req: DailyGotoRequest) -> dict:
    """Jump back (or forward) to a day by replaying the recorded feeds."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="会话未启动")
    try:
        snap = _serial(session.go_to_day, req.day)
        _save_daily(session, snap)
        return snap
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/daily/compare")
def api_daily_compare(req: DailyCompareRequest) -> dict:
    """Run several feeding plans from the same state for side-by-side curves."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="会话未启动")
    try:
        return _serial(session.compare, req.scenarios, req.days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/optimize")
def api_optimize(req: OptimizeRequest) -> dict:
    """Recommend a feed blend that maximises methane inside the operating window."""
    try:
        import optimize as opt_mod

        limits = opt_mod.Limits(**{k: float(v) for k, v in (req.limits or {}).items()})
        return _serial(
            opt_mod.optimize,
            ids=list(req.substrate_ids),
            current=list(req.flows),
            days=req.days,
            t_c=req.t_c,
            limits=limits,
            adjustable=[(i in (req.adjustable or req.substrate_ids)) for i in req.substrate_ids],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc



@app.get("/workspaces")
def workspaces_page() -> FileResponse:
    """Serve the workspace (long-session) manager."""
    return FileResponse(STATIC_DIR / "workspaces.html")


class WorkspaceRequest(BaseModel):
    """Create / rename a workspace."""

    name: str
    note: str = ""


@app.get("/api/workspaces")
def api_workspaces() -> dict:
    """All workspaces plus which one is active."""
    return {"ok": True, "active": WS.info(), "items": WS.list()}


@app.post("/api/workspaces")
def api_workspace_create(req: WorkspaceRequest) -> dict:
    """Create a workspace and make it active."""
    try:
        rec = WS.create(req.name.strip() or "未命名工作区", req.note)
        WS.open(rec["id"])
        _DAILY["session"] = None
        _SENSORS["hub"] = None
        return {"ok": True, **rec}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/workspaces/{ws_id}/open")
def api_workspace_open(ws_id: str) -> dict:
    """Switch the active workspace and drop cached sessions."""
    try:
        rec = WS.open(ws_id)
        _DAILY["session"] = None
        _SENSORS["hub"] = None
        return {"ok": True, **rec}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/workspaces/{ws_id}/rename")
def api_workspace_rename(ws_id: str, req: WorkspaceRequest) -> dict:
    """Rename a workspace."""
    try:
        return {"ok": True, **WS.rename(ws_id, req.name)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/workspaces/{ws_id}")
def api_workspace_delete(ws_id: str) -> dict:
    """Delete a workspace and everything stored in it."""
    was_active = WS.active_id == ws_id
    ok = WS.delete(ws_id)
    if was_active:
        _DAILY["session"] = None
        _SENSORS["hub"] = None
    return {"ok": ok}


@app.get("/api/daily/export.csv")
def api_daily_export() -> Response:
    """Download the run record as CSV (a shift report a plant can file)."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="还没有会话")
    snap = session.snapshot()
    s = snap["series"]
    names = snap.get("feed_names") or {}

    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["天数", "罐龄(d)", "沼气(m³/d)", "甲烷(m³/d)", "甲烷占比(%)", "pH",
              "VFA(kg/m³)", "TAC(kg/m³)", "总进料(m³/d)", "OLR(kg VS/(m³·d))", "HRT(d)"]
    header += [f"投料-{names.get(i, i)}(m³/d)" for i in snap["feed"]]
    w.writerow(header)

    feed_cols = {i: [] for i in snap["feed"]}
    for h in session.history:
        for i, v in (h.get("feed") or {}).items():
            feed_cols.setdefault(i, []).append(v)
    for k in feed_cols:
        col = feed_cols[k]
        feed_cols[k] = col + [None] * (len(s["day"]) - len(col))

    for idx, day in enumerate(s["day"]):
        ages = s.get("t_days") or []
        row = [
            int(day),
            round(ages[idx], 2) if idx < len(ages) else "",
            s["q_gas"][idx], s["q_ch4"][idx],
            round(s["ch4_pct"][idx], 2) if s["ch4_pct"][idx] is not None else "",
            round(s["ph"][idx], 3), round(s["vfa"][idx], 3), round(s["tac"][idx], 3),
            s["q_total"][idx], round(s["olr"][idx], 2), round(s["hrt"][idx], 1),
        ]
        for i in snap["feed"]:
            row.append(feed_cols[i][idx] if idx < len(feed_cols[i]) else "")
        w.writerow(row)

    body = "\ufeff" + buf.getvalue()  # BOM：Excel 打开中文不乱码
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="daily-report.csv"'},
    )


class PerturbRequest(BaseModel):
    """Drift the plant parameter by a factor (a simulated process fault)."""

    factor: float = Field(default=1.3, ge=0.1, le=10.0)
    parameter: str = "k_m_ac"


@app.post("/api/sensors/perturb")
def api_sensors_perturb(req: PerturbRequest) -> dict:
    """Simulate a reality change on the workspace plant (was /online's drift)."""
    session = _ws_daily()
    if session is None:
        raise HTTPException(status_code=409, detail="还没有会话：先在「按天推进」启动一个工作区会话")
    try:
        snap = _serial(session.perturb, req.factor, req.parameter)
        _save_daily(session, snap)
        return snap
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/diagnose")
def api_diagnose() -> dict:
    """Rule-based diagnosis of the current state: what is wrong and what to do."""
    session = _ws_daily()
    if session is None:
        return {"ok": False, "level": "unknown", "headline": "还没有会话", "causes": [], "evidence": {}}
    try:
        import diagnose as diag

        return diag.diagnose(session.snapshot())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/diagnose/demo")
def api_diagnose_demo() -> dict:
    """Canned fault scenarios, run through the same rules as the live path."""
    try:
        import diagnose as diag

        return {"ok": True, "items": diag.list_demo(), "note": "范例为合成数据，用于演示判断逻辑，不是现场记录。"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/health")
def health() -> dict:
    """Liveness probe."""
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="PyADM1ODE 可视化操作平台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8848)
    args = parser.parse_args()

    import uvicorn

    print(f"\n  PyADM1ODE 可视化平台  →  http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
