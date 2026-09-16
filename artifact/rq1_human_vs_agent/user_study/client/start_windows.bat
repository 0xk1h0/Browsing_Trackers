@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
cls

echo ========================================
echo   Browser Privacy User Study
echo ========================================
echo.

REM --- Check Python ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo         Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo [OK] Python found

REM --- Check/Install mitmproxy ---
where mitmdump >nul 2>&1
if %errorlevel% neq 0 (
    echo [SETUP] Installing mitmproxy... (first time only)
    python -m pip install mitmproxy
    echo.
)
echo [OK] mitmproxy ready

REM --- Get participant ID ---
echo.
set /p PID="Enter your participant ID (e.g. P001): "

if "%PID%"=="" (
    echo [ERROR] No ID entered.
    pause
    exit /b 1
)

echo.
echo Starting study for %PID%...
echo.
python study_client.py --participant %PID%

echo.
pause
