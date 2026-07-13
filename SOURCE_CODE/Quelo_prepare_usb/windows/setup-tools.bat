@echo off
setlocal
cd /d "%~dp0"
set "DEST=%~dp0tools\e2fsprogs"
set "CYG=C:\cygwin64\bin"

if not exist "%DEST%" mkdir "%DEST%"

if exist "%CYG%\mke2fs.exe" (
  echo Copia mke2fs da Cygwin64...
  copy /Y "%CYG%\mke2fs.exe" "%DEST%\" >nul
  for %%D in (cygwin1.dll cyggcc_s-seh-1.dll cygiconv-2.dll cygintl-8.dll cygpcre-1.dll) do (
    if exist "%CYG%\%%D" copy /Y "%CYG%\%%D" "%DEST%\" >nul
  )
  echo Strumenti copiati in %DEST%
  exit /b 0
)

echo.
echo mke2fs non trovato in Cygwin64.
echo.
echo Opzione 1 — installa Cygwin64 e il pacchetto e2fsprogs, poi rilancia questo script.
echo   https://www.cygwin.com/
echo.
echo Opzione 2 — copia manualmente mke2fs.exe e le DLL Cygwin in:
echo   %DEST%
echo.
echo Vedi README-WINDOWS.txt per i dettagli.
pause
exit /b 1
