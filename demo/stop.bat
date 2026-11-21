@echo off
chcp 65001 >nul
rem Stop whatever is listening on the demo port.
set PORT=8848

powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $c) { exit 1 } else { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; exit 0 }"
if errorlevel 1 (
    echo Nothing is listening on port %PORT%.
) else (
    echo Stopped the demo on port %PORT%.
)
ping -n 3 127.0.0.1 >nul
