#!/usr/bin/env python3
"""Costanti e utilità condivise prepare-usb (Linux + Windows)."""

from __future__ import annotations

import os
from typing import Callable

QUELO_PUBLISH_ISO_VERSION = "0.71"
PERSIST_LABEL = "persistence"
HOME_LABEL = "QUELO-HOME"
PERSIST_SIZES_MB = (512, 1024, 2048)

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
