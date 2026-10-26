@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   france2subs - start
echo ============================================
echo.

REM Find a base interpreter just to run launch.py (launch.py then picks
REM the best one that actually has tkinter + dependencies).
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python not found.
    echo         Install Python 3.9+ from https://www.python.org/downloads/
    echo         and tick "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

%PY% "%~dp0launch.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Startup failed. See the message above.
    echo         Run install.bat first if dependencies are missing.
    pause
)

exit /b %RC%
