@echo off
REM launch_kiosk.bat — Windows Kiosk Launcher for NemoHeadUnit WebCodecs Frontend
REM Strict compliance with docs/new-pattern.md

set TARGET_URL=%1
if "%TARGET_URL%"=="" set TARGET_URL=http://127.0.0.1:8000

echo Launching Windows WebCodecs Kiosk pointing to %TARGET_URL%...

start msedge --app="%TARGET_URL%" ^
    --enable-accelerated-video-decode ^
    --use-gl=angle ^
    --js-flags="--max-old-space-size=256" ^
    --autoplay-policy=no-user-gesture-required
