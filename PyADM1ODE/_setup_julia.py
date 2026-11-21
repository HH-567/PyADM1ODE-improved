"""Bootstrap Julia + OrdinaryDiffEq for the PyADM1ODE ADM1jl backend.

Run with the project venv:
    .venv\\Scripts\\python.exe _setup_julia.py

First run downloads a Julia runtime (~300 MB) via juliacall, then installs the
SciML solver packages that ADM1jl itself uses (Rodas4P lives in
OrdinaryDiffEqRosenbrock).
"""

import sys
import time
from pathlib import Path

t0 = time.time()
from juliacall import Main as jl  # noqa: E402  (downloads Julia on first import)

print(f"[1/3] Julia runtime ready: {jl.seval('VERSION')}  ({time.time() - t0:.1f}s)", flush=True)

jl.seval("using Pkg")

t1 = time.time()
jl.seval("""
    Pkg.add([
        "OrdinaryDiffEqRosenbrock",
        "OrdinaryDiffEqBDF",
        "SciMLBase",
    ])
    """)
print(f"[2/4] packages added ({time.time() - t1:.1f}s)", flush=True)

# The ADM1da port lives in pyadm1/core/julia/PyADM1Julia as a real package so
# Julia can cache its native code. Without this the backend falls back to
# recompiling ~5.7 s on every process start.
t_pkg = time.time()
pkg_path = (Path(__file__).parent / "pyadm1" / "core" / "julia" / "PyADM1Julia").as_posix()
jl.seval(f'Pkg.develop(path = "{pkg_path}")')
print(f"[3/4] PyADM1Julia developed ({time.time() - t_pkg:.1f}s)", flush=True)

t2 = time.time()
jl.seval("Pkg.precompile()")
print(f"[4/4] precompiled ({time.time() - t2:.1f}s)", flush=True)

# Pkg.precompile() can exit without writing *native* code caches, and then every
# Julia start recompiles OrdinaryDiffEq from scratch - measured 31.7 s cold
# instead of 9.5 s. Verify the caches actually landed.
_depot = Path.home() / ".julia" / "compiled"
_native = (list(_depot.rglob("*.dll")) + list(_depot.rglob("*.so"))) if _depot.exists() else []
print(f"[check] native code caches under {_depot}: {len(_native)}", flush=True)
if not _native:
    print(
        "[warn] no native caches were written; Julia will recompile the stack on\n"
        "       every start. Re-run this script to fix it.",
        flush=True,
    )

print("Rodas4P available:", jl.seval("using OrdinaryDiffEqRosenbrock; Rodas4P() isa Any"))
print(f"total {time.time() - t0:.1f}s")
sys.exit(0)
