@echo off
REM Copia mke2fs da Cygwin locale se installato (opzionale, per sviluppatori).
setlocal
cd /d "%~dp0"
set "DEST=%~dp0..\tools\e2fsprogs"

if exist "%DEST%\mke2fs.exe" (
  echo mke2fs gia presente in %DEST%
  exit /b 0
)

for %%C in ("C:\cygwin\bin" "C:\cygwin64\bin") do (
  if exist "%%~\mke2fs.exe" (
    if not exist "%DEST%" mkdir "%DEST%"
    copy /Y "%%~\mke2fs.exe" "%DEST%\" >nul
    for %%D in (cygwin1.dll cygcom_err-2.dll cyge2p-2.dll cygext2fs-2.dll cygblkid-1.dll cygintl-8.dll cyguuid-1.dll cyggcc_s-1.dll cyggcc_s-seh-1.dll) do (
      if exist "%%~\%%D" copy /Y "%%~\%%D" "%DEST%\" >nul
    )
    echo Strumenti copiati in %DEST%
    exit /b 0
  )
)

echo mke2fs non trovato. Usa AVVIA.bat o il pacchetto offline completo.
pause
exit /b 1
