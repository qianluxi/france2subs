@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   france2subs - install
echo ============================================
echo.

REM ---------- 1. locate Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.9+ and check "Add to PATH".
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

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
echo [3/3] Pre-downloading translation models ...
python -c "import argostranslate.package as pkg, argostranslate.translate as tr; [x.download() for x in pkg.get_available_packages() if (x.from_code=='fr' and x.to_code=='en') or (x.from_code=='en' and x.to_code=='zh')]; print('done')"
if errorlevel 1 (
    echo [WARN] model download failed, will retry on first run.
)

echo.
echo ============================================
echo   Install done.
echo   GUI:      python app.py
echo   CLI:      python france2subs.py "URL"
echo ============================================
pause
