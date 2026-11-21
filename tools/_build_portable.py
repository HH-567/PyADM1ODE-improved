"""Build a truly portable demo: own Python + bundled packages, no install step.

Layout::

    PyADM1ODE-portable/
      python/            embeddable CPython 3.12 (from python.org)
      libs/              numpy / scipy / pandas / networkx / fastapi / uvicorn
      app/               pyadm1 + webdemo + both companion packages (as source)
      run.py             sys.path setup, then starts the web app
      start.bat / stop.bat / README.md / images/

Julia is deliberately absent: it would add ~1 GB and the demo works without it
(the compare button reports that the Julia backend is unavailable).
"""

import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(".")
OUT = ROOT / "PyADM1ODE-portable"
SP = ROOT / "PyADM1ODE" / ".venv" / "Lib" / "site-packages"

PY_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"

# Runtime deps actually imported by the demo. matplotlib and torch are out:
# importing pyadm1 does not pull either of them.
WANT_DIRS = [
    "numpy", "numpy.libs", "numpy-2", "numpy_",
    "scipy", "scipy.libs",
    "pandas", "pandas.libs",
    "networkx",
    "fastapi", "starlette", "anyio", "sniffio", "h11", "click",
    "pydantic", "pydantic_core", "annotated_types", "typing_inspection",
    "yaml",
]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "docs", "tests",
             "benchmark", "site", ".github", "git-cliff", "experiments", "datasets",
             "notebooks", "dlls"}


def copytree(src: Path, dst: Path) -> int:
    n = 0
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or p.is_dir():
            continue
        if p.suffix in {".pyc", ".pyo"}:
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
        n += 1
    return n


def wanted(name: str) -> bool:
    low = name.lower()
    if low.startswith(("torch", "matplotlib", "juliacall", "juliapkg", "pythoncall",
                       "pyadm1", "sympy", "fsspec", "mpmath", "setuptools")):
        return False
    if not name.endswith(".dist-info"):
        return any(low == d or low.startswith(d.replace("-2", "-")) for d in WANT_DIRS)
    return False


def dependency_closure(site_packages: Path) -> tuple[set[str], set[str]]:
    """Import the demo's stack and return (top-level module dirs, dist names).

    Walking the actual import graph beats guessing: fastapi alone pulls
    annotated_doc, starlette, anyio and friends, and missing one is only found
    when the bundle fails to start.
    """
    import importlib
    import importlib.metadata as md
    import sys

    site_packages = site_packages.resolve()  # module files are absolute paths
    modules = [
        "pyadm1",
        "fastapi",
        "uvicorn",
        "starlette",
        "pydantic",
        "yaml",
        "pandas",
        "numpy",
        "scipy",
        "networkx",
        "pyadm1ode_calibration",
        "pyadm1ode_estimation",
    ]
    for name in modules:
        importlib.import_module(name)

    dists: set[str] = set()
    dist_map = md.packages_distributions()
    for name, mod in list(sys.modules.items()):
        if not mod or not getattr(mod, "__file__", None):
            continue
        f = Path(mod.__file__).resolve()
        try:
            f.relative_to(site_packages)
        except ValueError:
            continue
        top = name.split(".")[0]
        for d in dist_map.get(top, []):
            dists.add(d.lower())

    top_dirs: set[str] = set()
    for name, mod in list(sys.modules.items()):
        if not mod or not getattr(mod, "__file__", None):
            continue
        f = Path(mod.__file__).resolve()
        try:
            f.relative_to(site_packages)
        except ValueError:
            continue
        top = name.split(".")[0]
        cand = site_packages / top
        if cand.is_dir():
            top_dirs.add(top)
        elif (site_packages / f"{top}.py").exists():
            top_dirs.add(f"{top}.py")
        # bundled shared libraries (numpy.libs, scipy.libs, ...)
        libs = site_packages / f"{top}.libs"
        if libs.is_dir():
            top_dirs.add(f"{top}.libs")
    return top_dirs, dists


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()

    # --- python -----------------------------------------------------------
    zpath = OUT / "_py.zip"
    print(f"  downloading {PY_URL}")
    urllib.request.urlretrieve(PY_URL, zpath)  # noqa: S310
    with zipfile.ZipFile(zpath) as z:
        z.extractall(OUT / "python")
    zpath.unlink()
    pth = next((OUT / "python").glob("python*._pth"))
    pth.write_text("python312.zip\n.\n..\\libs\nimport site\n", encoding="ascii")
    print(f"  python: {(OUT / 'python').name}, _pth = {pth.name}")

    # --- libs -------------------------------------------------------------
    # Copy everything except a blocklist. Walking the import graph looked
    # tidier but misses lazily imported modules (uvicorn pulls h11 only when
    # the server starts), and a missing module only shows up at launch.
    libs = OUT / "libs"
    libs.mkdir()
    blocked = (
        "torch", "matplotlib", "juliacall", "juliapkg", "pythoncall", "python_call",
        "pyadm1", "pyadm1ode", "sympy", "fsspec", "mpmath", "setuptools", "pip",
        "wheel", "pytest", "_pytest", "coverage", "black", "ruff", "mypy", "isort",
        "flake8", "pycodestyle", "pyflakes", "nodeenv", "virtualenv", "distlib",
        "filelock", "platformdirs", "identify", "cfgv", "pre_commit",
    )
    taken = 0
    skipped = 0
    for item in sorted(SP.iterdir()):
        if item.name.startswith("__"):
            continue
        if item.name.lower().startswith(blocked):
            skipped += 1
            continue
        if item.name.endswith(".dist-info"):
            continue  # handled below
        dst = libs / item.name
        if item.is_dir():
            shutil.copytree(item, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, dst)
        taken += 1
    for item in sorted(SP.glob("*.dist-info")):
        if item.name.split("-")[0].lower().startswith(blocked):
            continue
        shutil.copytree(item, libs / item.name, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"  libs: {taken} entries (skipped {skipped}), "
          f"{(sum(p.stat().st_size for p in libs.rglob('*') if p.is_file()) / 1e6):.0f} MB")

    # --- app sources ------------------------------------------------------
    app = OUT / "app"
    src = ROOT / "PyADM1ODE"
    n1 = copytree(src / "pyadm1", app / "PyADM1ODE" / "pyadm1")
    n2 = copytree(src / "webdemo", app / "PyADM1ODE" / "webdemo")
    n3 = copytree(src / "data", app / "PyADM1ODE" / "data")
    for f in ("pyproject.toml", "LICENSE"):
        p = src / f
        if p.exists():
            shutil.copy2(p, app / "PyADM1ODE" / f)
    for name, inner in (("PyADM1ODE_calibration", "pyadm1ode_calibration"),
                        ("PyADM1ODE_estimate", "pyadm1ode_estimation")):
        copytree(ROOT / name / inner, app / name / inner)
    print(f"  app: pyadm1 {n1}, webdemo {n2}, data {n3}")

    # --- pyadm1 metadata so importlib.metadata.version() works ------------
    di = SP / "pyadm1-0.6.2.dist-info"
    meta = libs / "pyadm1-0.6.2.dist-info"
    meta.mkdir()
    if (di / "METADATA").exists():
        shutil.copy2(di / "METADATA", meta / "METADATA")
    else:
        (meta / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: pyadm1\nVersion: 0.6.2\n", encoding="utf-8")

    # --- launcher ---------------------------------------------------------
    for f in ("start.bat", "stop.bat"):
        (OUT / f).write_text(LAUNCHERS[f], encoding="ascii")
    (OUT / "run.py").write_text(RUNPY, encoding="utf-8")
    (OUT / "README.md").write_text(README, encoding="utf-8")
    (OUT / "images").mkdir()
    for png in (ROOT / "demo" / "images").glob("*.png"):
        shutil.copy2(png, OUT / "images" / png.name)

    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    count = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"\nportable: {OUT}  ({total / 1e6:.0f} MB, {count} files)")
    return 0


LAUNCHERS = {
    "stop.bat": r"""@echo off
chcp 65001 >nul
set PORT=8848
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $c) { exit 1 } else { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; exit 0 }"
if errorlevel 1 (echo Nothing is listening on port %PORT%.) else (echo Stopped the demo on port %PORT%.)
ping -n 3 127.0.0.1 >nul
""",
}

LAUNCHERS["start.bat"] = r"""@echo off
chcp 65001 >nul
rem  Double-click to start. Nothing to install.
set PYTHONUTF8=1
set PORT=8848
set HERE=%~dp0

if not exist "%HERE%python\python.exe" (
    echo [ERROR] python\python.exe is missing - the folder was not copied completely.
    pause
    exit /b 1
)

rem Already running? Just open it.
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:%PORT%/api/health'; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    start "" http://127.0.0.1:%PORT%
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

echo.
echo ================================================
echo   PyADM1ODE demo
echo   URL:  http://127.0.0.1:%PORT%
echo   The browser opens by itself once the server is ready
echo   (first start takes ~30 s, later ones are quick).
echo   Stop: Ctrl+C here, or run stop.bat
echo ================================================
echo.

"%HERE%python\python.exe" "%HERE%run.py" --port %PORT%
pause
"""

RUNPY = '''"""Start the demo using the bundled interpreter and libraries."""

import os
import socket
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")
for extra in (
    APP,
    os.path.join(APP, "PyADM1ODE"),
    os.path.join(APP, "PyADM1ODE_calibration"),
    os.path.join(APP, "PyADM1ODE_estimate"),
):
    if extra not in sys.path:
        sys.path.insert(0, extra)

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# figures are never rendered here; keeps matplotlib out of the picture entirely
os.environ.setdefault("MPLBACKEND", "Agg")


def _port() -> int:
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                pass
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                pass
    return 8848


def _open_when_ready(port: int, timeout: float = 120.0) -> None:
    """Open the browser only once the server answers.

    Launching it up front means the first thing the user sees is
    "cannot connect"; the first import of scipy/pandas takes a while.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
        time.sleep(0.4)


if __name__ == "__main__":
    port = _port()
    threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
    target = os.path.join(APP, "PyADM1ODE", "webdemo", "app.py")
    sys.argv = [target] + sys.argv[1:]
    runpy_target = target
    import runpy

    runpy.run_path(runpy_target, run_name="__main__")
'''

README = """# PyADM1ODE 演示（免安装）

厌氧消化（ADM1da，41 状态）的可视化演示：参数在页面上调、求解过程看得见。

## 启动

**双击 `start.bat`**，浏览器会自动打开 <http://127.0.0.1:8848>。

不用装 Python、不用装依赖、不用联网——这个文件夹里已经带好了运行环境。

关掉窗口即停止；或双击 `stop.bat`。

## 六个页面

| 页面 | 地址 | 一句话 |
| --- | --- | --- |
| 仪表盘 | `/` | 调配料、改参数、跑仿真、看曲线 |
| 求解引擎 | `/engine` | 全屏视觉：每一下跳动 = 求解器走的一步 |
| 三通道分步 | `/trace` | 三种积分器各跑一遍，共用时间轴对比 |
| 组件系统 | `/system` | 两级厂站，物料 / 沼气 / 能量三条通道 |
| 参数标定 | `/calibration` | 从数据反演一个动力学参数 |
| 状态估计 | `/estimation` | UKF 从产气/pH 反推内部状态 |

## 关于 Julia

这一份**不含 Julia 后端**（它要 1 GB 左右）。仪表盘右下角的「两求解器对比」
按钮会提示后端不可用，其余功能全部正常。

## 数据说明

页面上的"实测数据"是用模型自己生成的合成数据（真值已知），
用来演示标定与估计方法本身，不是真实厂站的测量记录。
"""

if __name__ == "__main__":
    raise SystemExit(main())
