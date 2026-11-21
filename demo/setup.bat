@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  One-time setup: create the Python environment, install the
rem  three packages, then bootstrap Julia.
rem  Needs network access. Takes roughly 5-10 minutes the first time
rem  (Julia itself is about 300 MB).
rem  Pure ASCII on purpose: cmd.exe mis-parses UTF-8 batch files
rem  after "chcp 65001".
rem ---------------------------------------------------------------
setlocal EnableExtensions
set PYTHONUTF8=1
rem Two layouts are supported by the same script:
rem   bundle   -> demo\app\PyADM1ODE   (built by tools\_build_bundle.py)
rem   in-repo  -> <repo root>\PyADM1ODE (this folder sits next to it)
set ROOT=%~dp0app\PyADM1ODE
if not exist "%ROOT%\pyproject.toml" set ROOT=%~dp0..\PyADM1ODE
set VENV=%ROOT%\.venv
set PY=%VENV%\Scripts\python.exe

echo.
echo ================================================
echo   PyADM1ODE demo - first-time setup
echo ================================================
echo.

rem --- Refuse to run from inside the archive ----------------------------
rem Windows lets you browse a .zip and double-click files in it. Those run
rem from a read-only temp mount, so venv creation always fails there.
echo %~dp0 | findstr /i /c:".zip" >nul
if not errorlevel 1 (
    echo [ERROR] This is running from inside the .zip archive.
    echo.
    echo         Right-click the zip  ^>  Extract All, then run setup.bat
    echo         from the extracted folder.
    echo.
    pause
    exit /b 1
)

rem --- The folder must be writable --------------------------------------
rem Check the file, not errorlevel: "echo" does not reset errorlevel, so a
rem previous failed command would leak into the test.
echo test> "%~dp0_~wtest.tmp" 2>nul
if not exist "%~dp0_~wtest.tmp" (
    echo [ERROR] This folder is not writable:
    echo         %~dp0
    echo         Move the extracted folder somewhere you own ^(Desktop^) and retry.
    pause
    exit /b 1
)
del "%~dp0_~wtest.tmp" >nul 2>nul

rem --- Pick an interpreter that actually runs ---------------------------
rem "python" on PATH can be the Microsoft Store stub, which opens the Store
rem instead of running anything. Test each candidate before trusting it.
set "PYEXE="
for %%c in ("python" "py -3.13" "py -3.12" "py -3.11" "py -3.10") do (
    if not defined PYEXE (
        %%~c -c "import sys" >nul 2>nul && set "PYEXE=%%~c"
    )
)
if not defined PYEXE (
    echo [ERROR] No working Python found.
    echo         Install Python 3.10-3.13 from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYEXE% --version 2^>^&1') do set "PYVER=%%v"
rem Keep the FIRST python.exe on PATH: later entries are often the Microsoft
rem Store stub, which opens the Store instead of running Python.
for /f "tokens=*" %%p in ('where python 2^>nul') do (
    if not defined PYPATH set "PYPATH=%%p"
)
echo Using %PYVER%   (%PYEXE%)
if defined PYPATH echo   python.exe: %PYPATH%
echo   target env: %VENV%
echo.

if not exist "%PY%" (
    echo [1/6] creating the virtual environment...
    %PYEXE% -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not create the virtual environment.
        echo         interpreter: %PYEXE%
        echo         target     : %VENV%
        echo.
        echo         Run this by hand in the same folder to see the real reason:
        echo             %PYEXE% -m venv "%VENV%"
        pause
        exit /b 1
    )
) else (
    echo [1/6] virtual environment already exists
)

if not exist "%PY%" (
    echo [ERROR] The environment was created but python.exe is missing:
    echo         %PY%
    pause
    exit /b 1
)

echo [2/6] installing the core package...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -e "%ROOT%"
if errorlevel 1 goto failed

echo [3/6] installing the web stack...
rem Plain uvicorn, not uvicorn[standard]: uvloop/httptools have no wheels on
rem some Python versions and would need a compiler.
"%PY%" -m pip install --quiet fastapi uvicorn juliacall
if errorlevel 1 goto failed

echo [4/6] installing the calibration package...
"%PY%" -m pip install --quiet --no-deps -e "%~dp0app\PyADM1ODE_calibration"
"%PY%" -m pip install --quiet sqlalchemy psycopg2-binary

echo [5/6] installing the estimation package...
"%PY%" -m pip install --quiet --no-deps -e "%~dp0app\PyADM1ODE_estimate"
if errorlevel 1 goto failed

echo [6/6] bootstrapping Julia (this is the slow part)...
call "%PY%" "%ROOT%\_setup_julia.py"
if errorlevel 1 goto failed

echo.
echo ================================================
echo   Setup complete. Double-click start.bat.
echo ================================================
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] a step failed - see the message above.
echo.
pause
exit /b 1
