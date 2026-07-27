@echo off
rem Launch Chrome in Kiosk Mode on Windows pointing to the local head unit UI.

set URL=%1
if "%URL%"=="" set URL=http://localhost:8080

start chrome.exe --kiosk --noerrdialogs --disable-infobars --autoplay-policy=no-user-gesture-required "%URL%"
