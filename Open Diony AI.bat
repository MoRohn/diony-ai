@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Diony AI - Windows launcher. Double-click.
rem
rem Opens the built app when this folder holds one (Build Diony AI.bat makes
rem it); otherwise runs the desk from source with this PC's Python 3.11+.
rem Either way a browser opens Studio at http://studio.diony.local.
cd /d "%~dp0"
echo Diony AI
echo A browser will open Studio at http://studio.diony.local
echo.
set BOOT=apps\desk\src\diony_desk\boot.py
if not exist "%BOOT%" (
  echo This file must stay in the Diony AI folder.
  pause
  exit /b 1
)

set VERSION=
for /f "usebackq delims=" %%v in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content 'packaging\desktop\version.json' -Raw | ConvertFrom-Json).version"`) do set VERSION=%%v
set APP=dist\desktop\%VERSION%\development\Diony AI\Diony AI.exe
if not "%VERSION%"=="" if exist "%APP%" (
  echo Opening the built Diony AI %VERSION%. ffmpeg and the runtime are inside it.
  echo You can close this window.
  start "" "%APP%"
  exit /b 0
)

echo No built app in this folder yet - running from source.
echo ^(Double-click Build Diony AI.bat once to build the app.^)
echo Leave this window open until it says you can close it.
echo.
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  for %%V in (3.14 3.13 3.12 3.11) do (
    py -%%V -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      py -%%V "%BOOT%"
      exit /b !ERRORLEVEL!
    )
  )
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    python "%BOOT%"
    exit /b !ERRORLEVEL!
  )
)
echo Diony AI needs Python 3.11 or newer to run from source.
echo Install it from https://www.python.org/downloads/windows/ then double-click this again,
echo or double-click Build Diony AI.bat to build the app, which needs no Python later.
start https://www.python.org/downloads/windows/
pause
exit /b 1
