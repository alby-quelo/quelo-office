@echo off
setlocal
cd /d "%~dp0\.."

where python >nul 2>&1
if errorlevel 1 (
  echo ERRORE: Python non trovato nel PATH.
  echo Installa Python 3.8+ da https://www.python.org/downloads/
  echo Durante l'installazione seleziona "tcl/tk and IDLE".
  pause
  exit /b 1
)

net session >nul 2>&1
if %errorLevel% NEQ 0 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'python' -ArgumentList '\"%~dp0..\prepare-usb-gui.py\"' -WorkingDirectory '%~dp0..' -Verb RunAs"
  exit /b 0
)

python "%~dp0..\prepare-usb-gui.py"
exit /b %errorLevel%
