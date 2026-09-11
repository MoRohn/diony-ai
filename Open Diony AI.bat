@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv
  if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import diony_contracts, diony_wizard, diony_producer, diony_director, uvicorn" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -e packages\contracts -e apps\wizard -e apps\producer -e apps\director
  if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" scripts\run_local.py
exit /b %errorlevel%

:error
echo Diony needs Python 3.11 or newer and could not prepare its local environment.
pause
exit /b 1
