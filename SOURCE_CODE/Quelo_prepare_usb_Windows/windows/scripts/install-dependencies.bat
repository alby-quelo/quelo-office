@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
set "WIN=%CD%\"
set "PYDIR=%WIN%python"
set "PYEXE=%PYDIR%\python.exe"
set "TCL_LIBRARY=%PYDIR%\tcl\tcl8.6"
set "TK_LIBRARY=%PYDIR%\tcl\tk8.6"
set "INST=%WIN%installers\"
set "TOOLS=%WIN%tools\e2fsprogs"
set "PYLOG=%WIN%logs\python-installer.log"
set "ERR=0"

call "%WIN%scripts\_logfull.cmd" INIT "%WIN%"
call "%WIN%scripts\_logfull.cmd" WRITE "========== install-dependencies.bat =========="
call "%WIN%scripts\_logfull.cmd" WRITE "WIN=%WIN%"
call "%WIN%scripts\_logfull.cmd" WRITE "PYDIR=%PYDIR%"
call "%WIN%scripts\_logfull.cmd" WRITE "INST=%INST%"

if exist "%PYEXE%" (
  call "%WIN%scripts\_logfull.cmd" WRITE "python.exe gia presente, verifica tkinter"
  "%PYEXE%" -c "import tkinter as tk; r=tk.Tk(); r.destroy(); print('tkinter OK')" >>"%WIN%LOG-FULL.txt" 2>&1
  if not errorlevel 1 (
    call "%WIN%scripts\_logfull.cmd" WRITE "Python + tkinter OK (preinstallato nel pacchetto)"
    goto :mke2fs
  )
  call "%WIN%scripts\_logfull.cmd" WRITE "tkinter fallito, provo correzione embed (_pth + _tkinter.pyd)"
  call :fix_embed_tkinter
  "%PYEXE%" -c "import tkinter as tk; r=tk.Tk(); r.destroy(); print('tkinter OK')" >>"%WIN%LOG-FULL.txt" 2>&1
  if not errorlevel 1 (
    call "%WIN%scripts\_logfull.cmd" WRITE "Python + tkinter OK (embed corretto)"
    goto :mke2fs
  )
)

if exist "%PYEXE%" (
  call "%WIN%scripts\_logfull.cmd" WRITE "tkinter ancora fallito, reinstallo con installer offline"
  goto :find_installer
)

:find_installer

set "PYINSTALLER="
for /f "delims=" %%F in ('dir /b "%INST%python-*.exe" 2^>nul') do (
  call "%WIN%scripts\_logfull.cmd" WRITE "Candidato installer: %%F"
  echo %%F | findstr /I "amd64 win_amd64" >nul
  if errorlevel 1 set "PYINSTALLER=!INST!%%F"
)

if not defined PYINSTALLER (
  call "%WIN%scripts\_logfull.cmd" WRITE "ERRORE: installer non trovato in %INST%"
  set ERR=1
  goto :end
)

call "%WIN%scripts\_logfull.cmd" WRITE "Installer: !PYINSTALLER!"

if exist "%PYDIR%" (
  call "%WIN%scripts\_logfull.cmd" WRITE "Disinstallo eventuale Python 3.9-32 gia registrato (evita MSI 1603)"
  "!PYINSTALLER!" /quiet /uninstall >>"%WIN%LOG-FULL.txt" 2>&1
  timeout /t 3 /nobreak >nul
  rmdir /s /q "%PYDIR%" 2>nul
)

if not exist "!PYINSTALLER!" (
  call "%WIN%scripts\_logfull.cmd" WRITE "ERRORE: file installer inesistente (percorso errato?)"
  set ERR=1
  goto :end
)

mkdir "%PYDIR%" 2>nul
call "%WIN%scripts\_logfull.cmd" WRITE "Avvio installer quiet + /log"

"!PYINSTALLER!" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_pip=0 Include_tcltk=1 Include_launcher=0 SimpleInstall=1 TargetDir="%PYDIR%" /log "%PYLOG%"
set "PY_RC=!ERRORLEVEL!"
call "%WIN%scripts\_logfull.cmd" WRITE "quiet exitcode=!PY_RC!"

if exist "%PYLOG%" type "%PYLOG%" >>"%WIN%LOG-FULL.txt"

if !PY_RC! NEQ 0 (
  call "%WIN%scripts\_logfull.cmd" WRITE "Installer quiet fallito, provo /passive"
  "!PYINSTALLER!" /passive InstallAllUsers=0 PrependPath=0 Include_test=0 Include_pip=0 Include_tcltk=1 Include_launcher=0 TargetDir="%PYDIR%" /log "%PYLOG%"
  set "PY_RC=!ERRORLEVEL!"
  call "%WIN%scripts\_logfull.cmd" WRITE "passive exitcode=!PY_RC!"
  if exist "%PYLOG%" type "%PYLOG%" >>"%WIN%LOG-FULL.txt"
)

if not exist "%PYEXE%" (
  call "%WIN%scripts\_logfull.cmd" WRITE "ERRORE: python.exe non creato (installer fallito)"
  dir /a "%PYDIR%" >>"%WIN%LOG-FULL.txt" 2>&1
  set ERR=1
  goto :end
)

"%PYEXE%" -c "import tkinter as tk; r=tk.Tk(); r.destroy(); print('tkinter OK')" >>"%WIN%LOG-FULL.txt" 2>&1
if errorlevel 1 (
  call "%WIN%scripts\_logfull.cmd" WRITE "ERRORE: tkinter non importabile"
  set ERR=1
  goto :end
)
call "%WIN%scripts\_logfull.cmd" WRITE "Python + tkinter installati"

:mke2fs
set "MKE2FS_OK=1"
if not exist "%TOOLS%\mke2fs.exe" set "MKE2FS_OK=0"
for %%D in (cygwin1.dll cygcom_err-2.dll cyge2p-2.dll cygext2fs-2.dll cygblkid-1.dll cygintl-8.dll cyguuid-1.dll cygiconv-2.dll cyggcc_s-1.dll) do (
  if not exist "%TOOLS%\%%D" set "MKE2FS_OK=0"
)
if "!MKE2FS_OK!"=="0" (
  call "%WIN%scripts\_logfull.cmd" WRITE "ERRORE: mke2fs/DLL mancanti"
  dir /a "%TOOLS%" >>"%WIN%LOG-FULL.txt" 2>&1
  set ERR=1
  goto :end
)
"%TOOLS%\mke2fs.exe" -V >>"%WIN%LOG-FULL.txt" 2>&1
if errorlevel 1 (
  call "%WIN%scripts\_logfull.cmd" WRITE "ERRORE: mke2fs non eseguibile (DLL?)"
  set ERR=1
  goto :end
)
call "%WIN%scripts\_logfull.cmd" WRITE "mke2fs OK"

>>"%WIN%LOG-FULL.txt" echo PROCESSOR_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%
>>"%WIN%LOG-FULL.txt" echo PROCESSOR_ARCHITEW6432=%PROCESSOR_ARCHITEW6432%
"%PYEXE%" -c "import struct,sys; print('Python',sys.version.split()[0], struct.calcsize('P')*8, 'bit')" >>"%WIN%LOG-FULL.txt" 2>&1
if errorlevel 1 (
  call "%WIN%scripts\_logfull.cmd" WRITE "ATTENZIONE: verifica architettura Python fallita"
)
goto :end

:fix_embed_tkinter
set "PTH=%PYDIR%\python39._pth"
if not exist "%PTH%" goto :eof
findstr /I /X "DLLs" "%PTH%" >nul 2>&1
if errorlevel 1 (
  call "%WIN%scripts\_logfull.cmd" WRITE "Aggiungo DLLs a python39._pth"
  >>"%PTH%" echo DLLs
)
if exist "%PYDIR%\DLLs\_tkinter.pyd" if not exist "%PYDIR%\_tkinter.pyd" (
  call "%WIN%scripts\_logfull.cmd" WRITE "Copio _tkinter.pyd nella root embed"
  copy /Y "%PYDIR%\DLLs\_tkinter.pyd" "%PYDIR%\_tkinter.pyd" >nul
)
goto :eof

:end
call "%WIN%scripts\_logfull.cmd" WRITE "========== install-dependencies FINE !ERR! =========="
exit /b !ERR!
