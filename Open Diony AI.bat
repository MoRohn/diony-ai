@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Double-click. First open installs the desk. After that it opens Studio.
cd /d "%~dp0"
echo Diony AI
echo A browser will open Studio at http://studio.diony.local
echo Leave this window open until it says you can close it.
echo.
set BOOT=apps\desk\src\diony_desk\boot.py
if not exist "%BOOT%" (
  echo This file must stay in the Diony AI folder.
  pause
  exit /b 1
)
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
echo Diony AI needs Python 3.11 or newer.
echo Install it from https://www.python.org/downloads/windows/ then double-click this again.
start https://www.python.org/downloads/windows/
pause
exit /b 1
