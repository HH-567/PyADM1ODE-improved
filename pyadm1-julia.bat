@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  PyADM1ODE launcher with the ADM1jl / Rodas4P Julia backend.
rem  Pure ASCII on purpose: cmd.exe mis-parses UTF-8 batch files
rem  after "chcp 65001".
rem ---------------------------------------------------------------
set PYTHONUTF8=1
set PYADM1_SOLVER=adm1jl
cd /d "%~dp0PyADM1ODE"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found in %CD%
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo.
echo ================================================
echo   PyADM1ODE - JULIA backend (PYADM1_SOLVER=adm1jl)
echo   Model   : 41-state ADM1da (unchanged)
echo   Solver  : ADM1jl's Rodas4P
echo   Python:
python --version
echo ------------------------------------------------
echo   First run in a process compiles Julia (~5 s).
echo   Try:
echo     python examples\01_basic_digester.py
echo     python _bench_adm1jl.py 150 5   A/B vs scipy
echo   Back to scipy: set PYADM1_SOLVER=scipy
echo   Leave with: exit
echo ================================================
echo.

cmd /k
