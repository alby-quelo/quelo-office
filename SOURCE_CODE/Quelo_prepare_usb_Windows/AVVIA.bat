@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "ROOT=%~dp0"
set "WIN=%ROOT%windows\"
set "SCRIPTS=%WIN%scripts\"
set "PYDIR=%WIN%python"
set "PYEXE=%PYDIR%\python.exe"
set "TCL_LIBRARY=%PYDIR%\tcl\tcl8.6"
set "TK_LIBRARY=%PYDIR%\tcl\tk8.6"
set "ERR=0"

call "%SCRIPTS%_logfull.cmd" INIT "%WIN%"
call "%SCRIPTS%_logfull.cmd" WRITE "========== AVVIA.bat =========="
call "%SCRIPTS%_logfull.cmd" WRITE "argv=%* ROOT=%ROOT%"

ver >>"%WIN%LOG-FULL.txt" 2>&1
whoami >>"%WIN%LOG-FULL.txt" 2>&1
>>"%WIN%LOG-FULL.txt" echo PROCESSOR_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%
net session >>"%WIN%LOG-FULL.txt" 2>&1

if /I not "%~1"=="elevated" (
  net session >nul 2>&1
  if errorlevel 1 (
    call "%SCRIPTS%_logfull.cmd" WRITE "Richiesta UAC"
    echo Richiesta UAC... Log: windows\LOG-FULL.txt
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -LiteralPath '%~f0' -ArgumentList 'elevated' -Verb RunAs -WorkingDirectory '%ROOT%'" >>"%WIN%LOG-FULL.txt" 2>&1
    timeout /t 5 /nobreak >nul
    exit /b 0
  )
)

net session >nul 2>&1
if errorlevel 1 (
  call "%SCRIPTS%_logfull.cmd" WRITE "ERRORE: serve Esegui come amministratore"
  set ERR=1
  goto :fail
)

echo Quelo Office prepare-usb - Log: windows\LOG-FULL.txt
call "%SCRIPTS%_logfull.cmd" WRITE "Admin OK, install-dependencies"

call "%SCRIPTS%install-dependencies.bat"
set ERR=!ERRORLEVEL!
if !ERR! NEQ 0 goto :fail

if not exist "%PYEXE%" (
  call "%SCRIPTS%_logfull.cmd" WRITE "ERRORE: python.exe mancante"
  set ERR=1
  goto :fail
)

"%PYEXE%" -c "import tkinter as tk; r=tk.Tk(); r.destroy()" >>"%WIN%LOG-FULL.txt" 2>&1
if errorlevel 1 (
  call "%SCRIPTS%_logfull.cmd" WRITE "ERRORE: tkinter"
  set ERR=1
  goto :fail
)

call "%SCRIPTS%_logfull.cmd" WRITE "Avvio GUI"
cd /d "%ROOT%"
"%PYEXE%" -u "%ROOT%prepare-usb-gui.py" >>"%WIN%LOG-FULL.txt" 2>&1
set ERR=!ERRORLEVEL!
if !ERR! NEQ 0 goto :fail
call "%SCRIPTS%_logfull.cmd" WRITE "========== OK =========="
exit /b 0

:fail
call "%SCRIPTS%_logfull.cmd" WRITE "========== ERRORE !ERR! =========="
echo ERRORE !ERR! - vedi windows\LOG-FULL.txt
pause
exit /b !ERR!
