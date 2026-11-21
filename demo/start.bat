@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  Start the PyADM1ODE demo and open it in the browser.
rem  Pure ASCII on purpose: cmd.exe mis-parses UTF-8 batch files
rem  after "chcp 65001".
rem ---------------------------------------------------------------
set PYTHONUTF8=1
set PORT=8848
set ROOT=%~dp0..\PyADM1ODE

if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [ERROR] Python environment not found in %ROOT%\.venv
    echo         Run PyADM1ODE\_setup_julia.py once to create it.
    pause
    exit /b 1
)

rem --- already running? then just open it -------------------------------
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

cd /d "%ROOT%"
start "" http://127.0.0.1:%PORT%
.venv\Scripts\python.exe webdemo\app.py --port %PORT%

pause
