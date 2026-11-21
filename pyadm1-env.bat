@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  PyADM1ODE launcher  (keep this file pure ASCII on purpose:
rem  cmd.exe mis-parses UTF-8 batch files after "chcp 65001")
rem ---------------------------------------------------------------
set PYTHONUTF8=1
cd /d "%~dp0PyADM1ODE"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found in %CD%
    echo Recreate it with:
    echo     uv venv --python 3.12 .venv
    echo     set VIRTUAL_ENV=%CD%\.venv
    echo     uv pip install -e .
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo.
echo ================================================
echo   PyADM1ODE - environment activated
echo   Python:
python --version
echo ------------------------------------------------
echo   Try:
echo     python main.py                      30-day demo
echo     python examples\01_basic_digester.py
echo     python examples\02_two_stage_plant.py
echo     pytest tests -q --no-cov             817 tests
echo   Julia backend (ADM1jl/Rodas4P, 5.8x faster):
echo     set PYADM1_SOLVER=adm1jl
echo   Leave with: exit
echo ================================================
echo.

cmd /k
