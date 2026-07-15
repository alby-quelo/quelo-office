#!/usr/bin/env python3
"""Costanti e utilità condivise prepare-usb (Linux + Windows)."""

from __future__ import annotations

import os
from typing import Callable

QUELO_PUBLISH_ISO_VERSION = "0.71"
PERSIST_LABEL = "persistence"
HOME_LABEL = "QUELO-HOME"
PERSIST_SIZES_MB = (512, 1024, 2048)

# Copiato su QUELO-HOME: nasconde ISO/EFI in Windows SENZA cambiare tipi MBR (boot ok).
WIN_BOOT_PROTECT_BAT = r"""@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Quelo Office - nascondi ISO/EFI
echo.
echo Rimuove le lettere di unita da ISO live e EFI boot sulla chiavetta Quelo.
echo NON modifica le partizioni: il boot da USB resta valido.
echo Serve QUELO-HOME gia visibile in Esplora file. Esegui come Amministratore.
echo.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -LiteralPath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$v = Get-Volume -FileSystemLabel 'QUELO-HOME' -ErrorAction Stop;" ^
  "$dl = $v.DriveLetter; if (-not $dl) { throw 'Assegna una lettera a QUELO-HOME prima di eseguire.' };" ^
  "$disk = Get-Partition -DriveLetter $dl | Get-Disk;" ^
  "Write-Host ('Disco n.' + $disk.Number);" ^
  "foreach ($pn in 1,2) {" ^
  "  $p = Get-Partition -DiskNumber $disk.Number -PartitionNumber $pn -ErrorAction SilentlyContinue;" ^
  "  if (-not $p) { continue };" ^
  "  if ($p.DriveLetter) { mountvol ($p.DriveLetter + ':') /D | Out-Null };" ^
  "  Set-Partition -DiskNumber $disk.Number -PartitionNumber $pn -IsHidden $true -ErrorAction SilentlyContinue;" ^
  "  Write-Host ('Partizione ' + $pn + ': nascosta in Esplora file');" ^
  "}" ^
  "Write-Host 'Completato.'"
echo.
pause
"""

WIN_BOOT_PROTECT_TXT = """Quelo Office — partizioni boot su Windows
======================================

ISO live (part. 1) e EFI boot (part. 2) servono per avviare la chiavetta:
NON formattarle e NON cambiarne il tipo in Gestione disco.

Su Windows possono comparire come unita aggiuntive. Per nasconderle SENZA
rompere il boot, esegui (tasto destro -> Esegui come amministratore):

  NASCONDI-BOOT-WINDOWS.bat

I tuoi file restano su QUELO-HOME (exFAT).
"""

ProgressCallback = Callable[[int, int, int, str], None]


class PrepareError(Exception):
    pass


def script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def project_dir() -> str:
    return os.path.abspath(os.path.join(script_dir(), "..", ".."))


def publish_iso_path() -> str:
    return os.path.join(
        project_dir(),
        "ISO",
        f"Quelo_Office-{QUELO_PUBLISH_ISO_VERSION}-alpha.iso",
    )


def find_publish_iso() -> str | None:
    path = publish_iso_path()
    return path if os.path.isfile(path) else None


def human_size_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if val < 1024.0 or unit == "GiB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{n} B"


def validate_confirm_text(disk_name: str, typed_name: str, typed_phrase: str) -> None:
    if typed_name != disk_name:
        raise PrepareError("Conferma 1/2 errata: nome disco non corrisponde.")
    if typed_phrase.strip().upper() != "SI SCRIVI":
        raise PrepareError("Conferma 2/2 errata: devi digitare SI SCRIVI.")


def write_windows_boot_protect_files(home_mount: str) -> None:
    """File opzionali su QUELO-HOME (Windows): nascondi ISO/EFI senza toccare MBR."""
    bat_path = os.path.join(home_mount, "NASCONDI-BOOT-WINDOWS.bat")
    txt_path = os.path.join(home_mount, "LEGGIMI-BOOT-WINDOWS.txt")
    with open(bat_path, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(WIN_BOOT_PROTECT_BAT)
    with open(txt_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(WIN_BOOT_PROTECT_TXT)
