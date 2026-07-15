@echo off
setlocal EnableDelayedExpansion
if /I "%~1"=="INIT" (
  set "LOGFULL=%~2LOG-FULL.txt"
  if not exist "%~2logs" mkdir "%~2logs" 2>nul
  >>"%LOGFULL%" echo.
  >>"%LOGFULL%" echo ################################################################
  >>"%LOGFULL%" echo [%date% %time%] LOG-FULL
  endlocal & set "LOGFULL=%~2LOG-FULL.txt"
  exit /b 0
)
if /I "%~1"=="WRITE" (
  >>"%LOGFULL%" echo [%date% %time%] %~2
  echo %~2
  exit /b 0
)
exit /b 1
