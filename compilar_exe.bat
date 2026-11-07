@echo off
rem LOT Bot - genera dist\LOT-Bot\LOT-Bot.exe con doble clic.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "build\build_windows.ps1" %*
echo.
pause
