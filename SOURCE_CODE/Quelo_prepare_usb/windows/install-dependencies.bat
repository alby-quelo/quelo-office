@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Quelo Office prepare-usb - installazione dipendenze Windows
echo ============================================================
echo.

set "PYEXE="
set "PYARGS="

call :find_python
if defined PYEXE goto :have_python

echo [1/3] Python 3.8+ non trovato.
echo.
call :install_python
call :find_python
if not defined PYEXE (
  echo.
  echo ERRORE: Python ancora assente.
  echo Installa manualmente da https://www.python.org/downloads/
  echo Durante l'installazione:
  echo   - spunta "Add python.exe to PATH"
  echo   - spunta "tcl/tk and IDLE" ^(per tkinter^)
  goto :fail
)

:have_python
echo [1/3] Python: OK ^(!PYEXE! !PYARGS!^)
for /f "delims=" %%V in ('!PYEXE! !PYARGS! -c "import sys; print(sys.version.split()[0])" 2^>nul') do set PYVER=%%V
echo       Versione: !PYVER!

echo.
echo [2/3] Controllo tkinter...
!PYEXE! !PYARGS! -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo ERRORE: tkinter non disponibile.
  echo Reinstalla Python da python.org con l'opzione "tcl/tk and IDLE".
  goto :fail
)
echo       tkinter: OK

echo.
echo [3/3] Controllo mke2fs ^(formattazione ext4^)...
if exist "%~dp0tools\e2fsprogs\mke2fs.exe" (
  echo       mke2fs: OK ^(gia presente^)
  goto :all_ok
)

echo       mke2fs mancante, tentativo installazione...

if exist "C:\cygwin64\bin\mke2fs.exe" (
  call "%~dp0setup-tools.bat"
  if exist "%~dp0tools\e2fsprogs\mke2fs.exe" goto :mke2fs_ok
)

where powershell >nul 2>&1
if not errorlevel 1 (
  echo       Download automatico da mirror Cygwin...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch-e2fsprogs.ps1"
  if exist "%~dp0tools\e2fsprogs\mke2fs.exe" goto :mke2fs_ok
)

echo.
echo ERRORE: impossibile installare mke2fs automaticamente.
echo.
echo Prova una di queste opzioni:
echo   A^) Installa Cygwin64 con pacchetto e2fsprogs, poi rilancia questo script
echo   B^) Su Windows 10+: verifica connessione Internet e rilancia
echo   C^) Copia manualmente mke2fs.exe in windows\tools\e2fsprogs\
echo.
echo Vedi README-WINDOWS.txt
goto :fail

:mke2fs_ok
echo       mke2fs: OK

:all_ok
echo.
echo ============================================================
echo  Dipendenze pronte.
echo  Avvia la GUI con: prepare-usb-gui.bat
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1

:find_python
set "PYEXE="
set "PYARGS="
where python >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=python"
  set "PYARGS="
  exit /b 0
)
where py >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARGS=-3"
  exit /b 0
)
exit /b 1

:install_python
where winget >nul 2>&1
if not errorlevel 1 (
  echo Tentativo con winget ^(Python 3.12^)...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if not errorlevel 1 exit /b 0
)

where choco >nul 2>&1
if not errorlevel 1 (
  echo Tentativo con Chocolatey ^(python^)...
  choco install python -y
  if not errorlevel 1 exit /b 0
)

echo Apertura pagina download Python nel browser...
start "" "https://www.python.org/downloads/"
echo Dopo l'installazione, chiudi e riapri questo script.
pause
exit /b 1
