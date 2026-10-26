@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   france2subs - installer
echo ============================================
echo.

REM ---------- 1. locate Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install Python 3.9+ from https://www.python.org/downloads/
    echo         and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])"') do set PYVER=%%v
echo Python found: %PYVER%
echo.

echo [1/3] Installing packages ...
python -m pip install --upgrade pip
python -m pip install -U yt-dlp faster-whisper argostranslate static-ffmpeg
if errorlevel 1 (
    echo [ERROR] Package install failed. Check network and retry.
    pause
    exit /b 1
)

echo.
echo [2/3] Preparing ffmpeg ...
python -c "import static_ffmpeg; p = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise(); print('ffmpeg ready:', p[0])"
if errorlevel 1 (
    echo [ERROR] ffmpeg prepare failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Pre-downloading models ...
set DOWNLOAD_WHISPER=Y
set /p DOWNLOAD_WHISPER=Download speech model now? about 1.5 GB [Y/n]:
if /i "%DOWNLOAD_WHISPER%"=="n" (
    python install_warmup.py --no-whisper
) else (
    python install_warmup.py
)

echo.
echo ============================================
echo   Install done.
echo.
echo   GUI:  python app.py
echo   CLI:  python france2subs.py "URL"
echo ============================================
pause
