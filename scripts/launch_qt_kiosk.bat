@echo off
REM launch_qt_kiosk.bat — Native Windows Batch Launcher for NemoHeadUnit-Wireless Qt6 GUI Frontend
REM
REM Usage:
REM   scripts\launch_qt_kiosk.bat [--fullscreen]

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "WORKSPACE_DIR=%%~fI"

cd /d "%WORKSPACE_DIR%"

echo [launch_qt_kiosk.bat] Setting PYTHONPATH and PyQt6 DLL search PATH...
set "PYTHONPATH=%WORKSPACE_DIR%;%WORKSPACE_DIR%\backend;%PYTHONPATH%"

for /f "delims=" %%i in ('micromamba run -n NemoHeadUnit-Wireless python -c "import site; print(site.getsitepackages()[0])"') do (
    set "SITE_PACKAGES=%%i"
)

if exist "!SITE_PACKAGES!\PyQt6\Qt6\bin" (
    echo [launch_qt_kiosk.bat] Found PyQt6 DLL dir: !SITE_PACKAGES!\PyQt6\Qt6\bin
    set "PATH=!SITE_PACKAGES!\PyQt6\Qt6\bin;!PATH!"
)

set "FULLSCREEN_ARG="
if "%1"=="--fullscreen" (
    set "FULLSCREEN_ARG=--fullscreen"
)

echo [launch_qt_kiosk.bat] Starting Qt6 HeadUnit Frontend Module...
micromamba run -n NemoHeadUnit-Wireless python backend\modules\qt6_gui\main.py %FULLSCREEN_ARG%

if %ERRORLEVEL% NEQ 0 (
    echo [launch_qt_kiosk.bat] Notice: Qt6 launch returned error.
    echo Fix for Windows host: pip install --force-reinstall PyQt6 PyQt6-Qt6
    pause
)
