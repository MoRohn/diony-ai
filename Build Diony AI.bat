@echo off
setlocal EnableExtensions
rem Diony AI - build the Windows app from this folder and open it. Double-click.
rem Needs Python 3.11+ once (https://www.python.org/downloads/windows/); the
rem built app carries its own runtime and ffmpeg, so later opens need nothing.
cd /d "%~dp0"
echo Diony AI - building the Windows app. This takes a few minutes the first time.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\release\build-local-windows.ps1"
if errorlevel 1 (
  echo.
  echo The build did not finish. Read the lines above; the usual fix is installing Python 3.11 or newer.
  pause
  exit /b 1
)
echo.
echo The current Diony AI is open. An older local build is replaced automatically.
pause
exit /b 0
