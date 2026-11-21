@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  Start the PyADM1ODE visualization demo.
rem  Pure ASCII on purpose: cmd.exe mis-parses UTF-8 batch files
rem  after "chcp 65001".
rem ---------------------------------------------------------------
set PYTHONUTF8=1
set PORT=8848
cd /d "%~dp0PyADM1ODE"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found in %CD%
    pause
    exit /b 1
)

rem --- Already running? Then just open it, do not start a second one -------
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:%PORT%/api/health'; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    echo.
    echo ================================================
    echo   Already running - nothing to start.
    echo   URL: http://127.0.0.1:%PORT%
    echo   Opening it in your browser...
    echo ================================================
    echo.
    start "" http://127.0.0.1:%PORT%
    timeout /t 4 >nul
    exit /b 0
)

rem --- Port taken by something that is NOT our demo ------------------------
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo.
    echo [ERROR] Port %PORT% is in use by another program.
    echo.
    echo   Find the process:
    echo     netstat -ano ^| findstr :%PORT%
    echo   Then stop it ^(replace PID^):
    echo     taskkill /PID ^<pid^> /F
    echo.
    pause
    exit /b 1
)

echo.
echo ================================================
echo   PyADM1ODE - visualization demo
echo   URL:  http://127.0.0.1:%PORT%
echo   Stop: Ctrl+C in this window
echo ================================================
echo.

start "" http://127.0.0.1:%PORT%
.venv\Scripts\python.exe webdemo\app.py --port %PORT%

pause
