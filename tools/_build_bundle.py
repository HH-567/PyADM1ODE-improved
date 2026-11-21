"""Build the self-contained demo bundle at 先复刻/PyADM1ODE-demo.

Copies only what the demo needs to run: the pyadm1 library, the web app, the
substrate data and the two companion packages (their pages import them). Skips
.venv (2 GB, not relocatable), .git, caches and upstream docs.
"""

import shutil
from pathlib import Path

ROOT = Path(".")
BUNDLE = ROOT / "PyADM1ODE-demo"
APP = BUNDLE / "app"

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
             "docs", "tests", "benchmark", "site", ".github", "git-cliff", "experiments",
             "datasets", "notebooks", "data"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".tmp"}


def copy_tree(src: Path, dst: Path, skip_dirs: set[str]) -> int:
    n = 0
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if p.is_dir():
            continue
        if p.suffix in SKIP_SUFFIX:
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
        n += 1
    return n


def main() -> int:
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    APP.mkdir(parents=True)

    # --- pyadm1 -----------------------------------------------------------
    src = ROOT / "PyADM1ODE"
    dst = APP / "PyADM1ODE"
    n_pkg = copy_tree(src / "pyadm1", dst / "pyadm1", SKIP_DIRS | {"dlls"})
    n_web = copy_tree(src / "webdemo", dst / "webdemo", SKIP_DIRS)
    n_data = copy_tree(src / "data", dst / "data", SKIP_DIRS)
    for f in ("pyproject.toml", "_setup_julia.py", "README.md", "LICENSE"):
        p = src / f
        if p.exists():
            shutil.copy2(p, dst / f)
    print(f"  pyadm1: {n_pkg} files, webdemo: {n_web}, data: {n_data}")

    # --- companion packages ----------------------------------------------
    for name, inner in (
        ("PyADM1ODE_calibration", "pyadm1ode_calibration"),
        ("PyADM1ODE_estimate", "pyadm1ode_estimation"),
    ):
        s = ROOT / name
        d = APP / name
        c = copy_tree(s / inner, d / inner, SKIP_DIRS)
        for f in ("pyproject.toml", "README.md"):
            p = s / f
            if p.exists():
                shutil.copy2(p, d / f)
        print(f"  {name}: {c} files")

    # --- images + docs ----------------------------------------------------
    (BUNDLE / "images").mkdir()
    for png in (ROOT / "demo" / "images").glob("*.png"):
        shutil.copy2(png, BUNDLE / "images" / png.name)
    print(f"  images: {len(list((BUNDLE / 'images').glob('*.png')))}")

    # start.bat is written for the bundle layout: prefer the bundled env, fall
    # back to the dev environment on this machine so the folder runs immediately,
    # and only then refuse and point at setup.bat.
    start_bat = r"""@echo off
chcp 65001 >nul
rem  Start the PyADM1ODE demo and open it in the browser.
rem  Pure ASCII on purpose: cmd.exe mis-parses UTF-8 batch files after chcp.
set PYTHONUTF8=1
set PORT=8848
set ROOT=%~dp0app\PyADM1ODE
set PYEXE=%ROOT%\.venv\Scripts\python.exe

rem Fall back to the development environment if this copy has none (same code).
if not exist "%PYEXE%" if exist "%~dp0..\PyADM1ODE\.venv\Scripts\python.exe" (
    set PYEXE=%~dp0..\PyADM1ODE\.venv\Scripts\python.exe
    echo Using the environment next to this folder.
)

if not exist "%PYEXE%" (
    echo [ERROR] No environment found. Run setup.bat once first.
    pause
    exit /b 1
)

powershell -NoProfile -Command "try { $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:%PORT%/api/health'; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    echo.
    echo   Demo is already running - opening the browser.
    echo.
    start "" http://127.0.0.1:%PORT%
    ping -n 4 127.0.0.1 >nul
    exit /b 0
)

echo.
echo ================================================
echo   PyADM1ODE demo
echo   URL:  http://127.0.0.1:%PORT%
echo   Stop: Ctrl+C here, or run stop.bat
echo ================================================
echo.

start "" http://127.0.0.1:%PORT%
"%PYEXE%" "%ROOT%\webdemo\app.py" --port %PORT%

pause
"""
    (BUNDLE / "start.bat").write_text(start_bat, encoding="ascii")
    print("  start.bat written for the bundle layout")

    for f in ("stop.bat", "setup.bat"):
        p = ROOT / "demo" / f
        if p.exists():
            shutil.copy2(p, BUNDLE / f)

    readme = (ROOT / "demo" / "README.md").read_text(encoding="utf-8")
    readme = readme.replace(
        "双击 **`start.bat`**，浏览器会自动打开 <http://127.0.0.1:8848>。",
        "**这台机器上**：直接双击 `start.bat`（会自动复用旁边已有的运行环境）。\n\n"
        "**换一台机器**：先双击 `setup.bat` 建环境——需要联网，视网速 5–20 分钟，"
        "其中 Julia 约 300 MB。装一次即可，之后都直接双击 `start.bat`。\n\n"
        "启动后浏览器会自动打开 <http://127.0.0.1:8848>。",
    )
    (BUNDLE / "README.md").write_text(readme, encoding="utf-8")
    print("  README rewritten for the bundle")

    total = sum(p.stat().st_size for p in BUNDLE.rglob("*") if p.is_file())
    print(f"\nbundle: {BUNDLE}  ({total / 1e6:.1f} MB, "
          f"{sum(1 for p in BUNDLE.rglob('*') if p.is_file())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
