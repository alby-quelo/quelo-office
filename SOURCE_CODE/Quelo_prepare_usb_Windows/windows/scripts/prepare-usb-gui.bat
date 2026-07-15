@echo off
REM Wrapper interno: richiama AVVIA.bat nella cartella base del pacchetto.
cd /d "%~dp0"
call "%~dp0..\..\AVVIA.bat" %*
