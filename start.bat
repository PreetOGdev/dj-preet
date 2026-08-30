@echo off
title DJ-Preet - Discord Music Bot and Web Player
echo ================================================================
echo       DJ-Preet - Localhost Discord Music Bot and Dashboard
echo ================================================================
echo.

:: Check for Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found on your system!
    echo Please install Python 3.10+ from python.org and add it to PATH.
    pause
    exit /b 1
)

echo [1/3] Verifying dependencies...
python -m pip install -r requirements.txt --quiet --no-warn-script-location

echo [2/3] Freeing port 8000 if previously occupied...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>nul

echo [3/3] Starting DJ-Preet unified server...
echo.
echo ================================================================
echo   Web Dashboard: http://localhost:8000
echo   Discord Bot  : Connecting to Discord Gateway...
echo ================================================================
echo.

:: Open browser in background after 2 seconds
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"

:: Run the bot and web server
python main.py

pause
