#!/usr/bin/env python3
"""Libreria host prepare-usb per Windows 7+."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

import quelo_prepare_common as common

QUELO_PUBLISH_ISO_VERSION = common.QUELO_PUBLISH_ISO_VERSION
PERSIST_LABEL = common.PERSIST_LABEL
HOME_LABEL = common.HOME_LABEL
PERSIST_SIZES_MB = common.PERSIST_SIZES_MB
ProgressCallback = common.ProgressCallback
PrepareError = common.PrepareError
script_dir = common.script_dir
find_publish_iso = common.find_publish_iso
validate_confirm_text = common.validate_confirm_text
human_size_bytes = common.human_size_bytes


@dataclass(frozen=True)
class DiskInfo:
    path: str
    name: str
    size: str
    model: str
    transport: str
    is_usb: bool
    disk_number: int


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def require_host() -> None:
    return


def require_root() -> None:
    if not is_admin():
        raise PrepareError(
            "Servono privilegi amministratore.\n"
            "Avvia prepare-usb-gui.bat (richiede UAC) oppure esegui il prompt come amministratore."
        )


def _tools_dir() -> str:
    return os.path.join(script_dir(), "windows", "tools", "e2fsprogs")


def _mke2fs_exe() -> str:
    bundled = os.path.join(_tools_dir(), "mke2fs.exe")
    if os.path.isfile(bundled):
        return bundled
    for candidate in (
        r"C:\cygwin64\bin\mke2fs.exe",
        r"C:\cygwin\bin\mke2fs.exe",
    ):
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("mke2fs")
    if found:
        return found
    raise PrepareError(
        "mke2fs.exe non trovato.\n"
        "Esegui windows\\setup-tools.bat oppure vedi windows\\README-WINDOWS.txt"
    )


def missing_dependencies() -> list[str]:
    missing: list[str] = []
    if sys.version_info < (3, 8):
        missing.append("Python 3.8 o superiore")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        missing.append("tkinter (reinstalla Python con Tcl/Tk)")
    if shutil.which("diskpart") is None:
        missing.append("diskpart (componente Windows)")
    try:
        _mke2fs_exe()
    except PrepareError:
        missing.append("mke2fs.exe (e2fsprogs — vedi windows\\README-WINDOWS.txt)")
    return missing


def _run_capture(cmd: list[str], *, input_text: str | None = None) -> str:
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise PrepareError(f"Comando fallito ({' '.join(cmd)}):\n{out.strip()}")
    return out


def _run_diskpart(lines: list[str]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="ascii") as fh:
        fh.write("\n".join(lines))
        fh.write("\n")
        script = fh.name
    try:
        return _run_capture(["diskpart", "/s", script])
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def _wmic_rows(args: list[str]) -> list[dict[str, str]]:
    out = _run_capture(["wmic"] + args)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in re.split(r"\s{2,}", lines[0]) if h.strip()]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        vals = [v.strip() for v in re.split(r"\s{2,}", line) if v.strip()]
        if len(vals) < len(headers):
            continue
        rows.append(dict(zip(headers, vals)))
    return rows


def _system_disk_number() -> int | None:
    try:
        rows = _wmic_rows(["partition", "where", "BootPartition=True", "get", "DiskIndex"])
        for row in rows:
            val = row.get("DiskIndex", "").strip()
            if val.isdigit():
                return int(val)
    except PrepareError:
        pass
    try:
        rows = _wmic_rows(["logicaldisk", "where", "DeviceID='C:'", "get", "DeviceID"])
        if not rows:
            return None
        assoc = _run_capture(
            ["wmic", "logicaldisk", "where", "DeviceID='C:'", "assoc", "assocclass=Win32_LogicalDiskToPartition"]
        )
        m = re.search(r"Disk #(\d+)", assoc)
        if m:
            return int(m.group(1))
    except PrepareError:
        return None
    return None


def physical_drive_path(disk_number: int) -> str:
    return rf"\\.\PhysicalDrive{disk_number}"


def partition_path(disk_number: int, partition_number: int) -> str:
    return rf"\\.\Harddisk{disk_number}\Partition{partition_number}"


def root_disk() -> str | None:
    num = _system_disk_number()
    return physical_drive_path(num) if num is not None else None


def list_disks() -> list[DiskInfo]:
    rows = _wmic_rows(["diskdrive", "get", "Index,Size,Model,InterfaceType,MediaType"])
    disks: list[DiskInfo] = []
    for row in rows:
        idx_s = row.get("Index", "").strip()
        if not idx_s.isdigit():
            continue
        idx = int(idx_s)
        size_raw = row.get("Size", "").strip()
        try:
            size = human_size_bytes(int(size_raw))
        except ValueError:
            size = size_raw or "?"
        model = (row.get("Model") or "").strip() or "—"
        iface = (row.get("InterfaceType") or "").strip().upper()
        media = (row.get("MediaType") or "").strip().upper()
        is_usb = "USB" in iface or "REMOVABLE" in media or "EXTERNAL" in media
        disks.append(
            DiskInfo(
                path=physical_drive_path(idx),
                name=str(idx),
                size=size,
                model=model,
                transport=iface or "—",
                is_usb=is_usb,
                disk_number=idx,
            )
        )
    disks.sort(key=lambda d: d.disk_number)
    return disks


def disk_is_usb(disk: str) -> bool:
    m = re.search(r"PhysicalDrive(\d+)$", disk, re.I)
    if not m:
        return False
    num = int(m.group(1))
    for info in list_disks():
        if info.disk_number == num:
            return info.is_usb
    return False


def _disk_number(disk: str) -> int:
    m = re.search(r"PhysicalDrive(\d+)$", disk, re.I)
    if not m:
        raise PrepareError(f"Disco non valido: {disk}")
    return int(m.group(1))


def _partition_count(disk_number: int) -> int:
    out = _run_diskpart([f"select disk {disk_number}", "list partition"])
    return len(re.findall(r"^\s*Partition\s+\d+", out, re.M))


def _free_drive_letter() -> str:
    for letter in "QWERTYUIOPASDFGHJKLZXCVBNM":
        if letter == "C":
            continue
        if not os.path.exists(f"{letter}:\\"):
            return letter
    raise PrepareError("Nessuna lettera di unità libera.")


def _remove_drive_letters(disk_number: int, log: Callable[[str], None] | None) -> None:
    out = _run_diskpart([f"select disk {disk_number}", "list partition"])
    for m in re.finditer(r"Partition\s+(\d+)", out):
        pnum = m.group(1)
        detail = _run_diskpart(
            [
                f"select disk {disk_number}",
                f"select partition {pnum}",
                "detail partition",
            ]
        )
        letter_m = re.search(r"Letter:\s*([A-Z]):", detail, re.I)
        if not letter_m:
            continue
        letter = letter_m.group(1).upper()
        if log:
            log(f"Rimuovo lettera {letter}: dalla partizione {pnum}...")
        _run_diskpart(
            [
                f"select disk {disk_number}",
                f"select partition {pnum}",
                f"remove letter={letter}",
            ]
        )


def wipe_usb(disk: str, log: Callable[[str], None] | None = None) -> None:
    disk_number = _disk_number(disk)
    if log:
        log(f"Smonto volumi sul disco {disk_number}...")
    _remove_drive_letters(disk_number, log)
    if log:
        log("Cancello tabella partizioni (diskpart clean)...")
    _run_diskpart([f"select disk {disk_number}", "clean"])
    time.sleep(2)
    if log:
        log("Chiavetta azzerata: pronta per scrittura ISO.")


def settle_usb_before_partition(disk: str, log: Callable[[str], None] | None = None) -> bool:
    disk_number = _disk_number(disk)
    for attempt in range(5):
        _remove_drive_letters(disk_number, log)
        time.sleep(1)
        out = _run_diskpart([f"select disk {disk_number}", "list partition"])
        still_assigned = False
        for m in re.finditer(r"Partition\s+(\d+)", out):
            pnum = m.group(1)
            detail = _run_diskpart(
                [
                    f"select disk {disk_number}",
                    f"select partition {pnum}",
                    "detail partition",
                ]
            )
            if re.search(r"Letter:\s*[A-Z]:", detail, re.I):
                still_assigned = True
                break
        if not still_assigned:
            return True
        if log:
            log(f"Disco ancora con lettere assegnate (tentativo {attempt + 1}/5)...")
        time.sleep(1)
    return False


def reread_partition_table(disk: str) -> None:
    time.sleep(2)


def load_write_iso_module():
    import importlib.util

    path = os.path.join(script_dir(), "quelo-write-iso.py")
    spec = importlib.util.spec_from_file_location("quelo_write_iso", path)
    if spec is None or spec.loader is None:
        raise PrepareError("quelo-write-iso.py non trovato.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def human_size(n: int) -> str:
    return human_size_bytes(n)


def write_iso(
    iso_path: str,
    disk: str,
    progress: ProgressCallback | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    def cb(pct: int, current: int, total: int, elapsed: int, label: str) -> None:
        if progress:
            progress(pct, current, total, label)

    if log:
        log(f"Scrittura ISO su {disk}...")
    mod = load_write_iso_module()

    def writer_progress(total, current, elapsed, label):
        pct = (current * 100 // total) if total else 0
        if label == "scrittura":
            mapped = 3 + int(pct * 72 / 100)
            msg = (
                "Fase 2 — Copia immagine ISO sulla chiavetta USB\n"
                f"Sto scrivendo l'immagine avviabile sul disco: {pct}% completato "
                f"({human_size(current)} di {human_size(total)}, {elapsed} s). "
                "È la fase più lunga: non scollegare la USB e non chiudere questa finestra."
            )
        elif label == "verifica":
            mapped = 75 + int(pct * 7 / 100)
            msg = (
                "Fase 3 — Controllo integrità della copia\n"
                f"Leggo la chiavetta e la confronto con il file ISO originale per "
                f"verificare che ogni byte sia stato copiato correttamente: {pct}% "
                f"({human_size(current)} / {human_size(total)})."
            )
        else:
            mapped = min(82, max(3, pct))
            msg = label
        cb(mapped, current, total, elapsed, msg)

    rc = mod.write_iso(iso_path, disk, use_direct=False, progress_callback=writer_progress)
    if rc != 0:
        raise PrepareError("Scrittura ISO fallita.")
    if log:
        log("Verifica ISO in corso...")
    rc = mod.verify_iso(iso_path, disk, progress_callback=writer_progress)
    if rc != 0:
        raise PrepareError("Verifica ISO fallita.")


def verify_new_partitions(disk_number: int, persist_num: int, home_num: int) -> tuple[str, str]:
    persist = partition_path(disk_number, persist_num)
    home = partition_path(disk_number, home_num)
    return persist, home


def create_partitions_auto(
    disk: str,
    persist_mb: int,
    log: Callable[[str], None] | None = None,
    step: Callable[[int, str], None] | None = None,
) -> tuple[str, str]:
    disk_number = _disk_number(disk)
    persist_num = home_num = 0
    for attempt in range(1, 4):
        if step:
            step(
                82 + attempt,
                "Fase 4 — Creazione partizioni per persistenza e dati utente\n"
                f"Tentativo {attempt} di 3: aggiungo due partizioni dopo l'area ISO — "
                f"una ext4 da {persist_mb} MB (etichetta «{PERSIST_LABEL}») e una exFAT "
                f"(etichetta «{HOME_LABEL}») con lo spazio rimanente.",
            )
        if log:
            log(f"Creo partizioni con diskpart (tentativo {attempt}/3)...")
        settle_usb_before_partition(disk, log)
        try:
            before = _partition_count(disk_number)
            _run_diskpart(
                [
                    f"select disk {disk_number}",
                    f"create partition primary size={persist_mb}",
                ]
            )
            persist_num = _partition_count(disk_number)
            _run_diskpart(
                [
                    f"select disk {disk_number}",
                    f"select partition {persist_num}",
                    "set id=83",
                ]
            )
            _run_diskpart([f"select disk {disk_number}", "create partition primary"])
            home_num = _partition_count(disk_number)
            if persist_num <= before or home_num <= persist_num:
                raise PrepareError("Numerazione partizioni inattesa dopo diskpart.")
            return verify_new_partitions(disk_number, persist_num, home_num)
        except PrepareError as exc:
            if log:
                log(str(exc))
            time.sleep(2)
    raise PrepareError("Creazione automatica partizioni fallita dopo 3 tentativi.")


def _format_exfat(partition_path: str, label: str) -> None:
    letter = _free_drive_letter()
    m = re.search(r"Harddisk(\d+)\\Partition(\d+)", partition_path, re.I)
    if not m:
        raise PrepareError(f"Percorso partizione non valido: {partition_path}")
    disk_n, part_n = m.group(1), m.group(2)
    _run_diskpart(
        [
            f"select disk {disk_n}",
            f"select partition {part_n}",
            f"assign letter={letter}",
        ]
    )
    try:
        proc = subprocess.run(
            ["format", f"{letter}:", "/FS:exFAT", f"/V:{label}", "/Q", "/Y"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise PrepareError(f"format exFAT fallito: {err}")
    finally:
        _run_diskpart(
            [
                f"select disk {disk_n}",
                f"select partition {part_n}",
                f"remove letter={letter}",
            ]
        )


def format_partitions(
    persist_part: str,
    home_part: str,
    log: Callable[[str], None] | None = None,
    step: Callable[[int, str], None] | None = None,
) -> None:
    mke2fs = _mke2fs_exe()
    tools = _tools_dir()
    env = os.environ.copy()
    if os.path.isdir(tools):
        env["PATH"] = tools + os.pathsep + env.get("PATH", "")

    if step:
        step(
            90,
            "Fase 5 — Formattazione partizione persistenza (ext4)\n"
            f"Preparo la partizione con filesystem ext4 ed etichetta «{PERSIST_LABEL}».",
        )
    if log:
        log(f"Formatto {persist_part} (ext4, {PERSIST_LABEL})...")
    proc = subprocess.run(
        [mke2fs, "-F", "-L", PERSIST_LABEL, persist_part],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise PrepareError(f"mkfs.ext4 fallito: {err}")

    if step:
        step(
            93,
            "Fase 5 — Formattazione partizione home (exFAT)\n"
            f"Preparo la partizione con filesystem exFAT ed etichetta «{HOME_LABEL}».",
        )
    if log:
        log(f"Formatto {home_part} (exFAT, {HOME_LABEL})...")
    _format_exfat(home_part, HOME_LABEL)


def setup_home_folders(home_part: str, log: Callable[[str], None] | None = None) -> None:
    m = re.search(r"Harddisk(\d+)\\Partition(\d+)", home_part, re.I)
    if not m:
        raise PrepareError(f"Partizione home non valida: {home_part}")
    disk_n, part_n = m.group(1), m.group(2)
    letter = _free_drive_letter()
    _run_diskpart(
        [
            f"select disk {disk_n}",
            f"select partition {part_n}",
            f"assign letter={letter}",
        ]
    )
    root = f"{letter}:\\"
    try:
        if log:
            log(f"Monto home exFAT su {letter}:\\...")
        home_dir = os.path.join(root, "home")
        os.makedirs(home_dir, exist_ok=True)
        for name in (
            "Desktop",
            "Documenti",
            "Scaricati",
            "Immagini",
            "Musica",
            "Video",
            "Modelli",
        ):
            os.makedirs(os.path.join(home_dir, name), exist_ok=True)
        os.makedirs(os.path.join(root, "quelo-export"), exist_ok=True)
        with open(os.path.join(home_dir, ".quelo-prepared"), "w", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
        if log:
            log("Cartelle home e quelo-export create.")
    finally:
        _run_diskpart(
            [
                f"select disk {disk_n}",
                f"select partition {part_n}",
                f"remove letter={letter}",
            ]
        )


def lsblk_tree(disk: str) -> str:
    disk_number = _disk_number(disk)
    return _run_diskpart([f"select disk {disk_number}", "list partition", "list volume"])


def run_prepare(
    iso_path: str,
    disk: str,
    persist_mb: int,
    *,
    allow_non_usb: bool = False,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    require_host()
    require_root()

    missing = missing_dependencies()
    if missing:
        raise PrepareError("Dipendenze mancanti: " + ", ".join(missing))

    if not os.path.isfile(iso_path):
        raise PrepareError(f"ISO non trovata: {iso_path}")
    if not disk.lower().startswith(r"\\.\physicaldrive"):
        raise PrepareError(f"Dispositivo non valido: {disk}")

    root = root_disk()
    if root and disk.lower() == root.lower():
        raise PrepareError("Hai scelto il disco di sistema. STOP.")

    if persist_mb not in PERSIST_SIZES_MB:
        raise PrepareError(f"Dimensione persistenza non valida: {persist_mb} MB")

    if not disk_is_usb(disk) and not allow_non_usb:
        raise PrepareError("Il disco selezionato non risulta USB/removable.")

    iso_name = os.path.basename(iso_path)

    def step(pct: int, msg: str) -> None:
        if progress:
            progress(pct, 0, 0, msg)

    step(
        0,
        "Fase 1 — Svuotamento completo della chiavetta USB\n"
        f"Preparo il disco {disk} alla scrittura: rimuovo lettere di unità e cancello "
        "la tabella partizioni. Tutto il contenuto precedente verrà eliminato.",
    )
    if log:
        log("PASSO 4/9 — Pulizia chiavetta")
    wipe_usb(disk, log)

    step(
        3,
        "Fase 2 — Copia immagine ISO sulla chiavetta USB\n"
        f"Trasferisco il file «{iso_name}» sul supporto rimovibile. "
        "Operazione lunga: non scollegare la chiavetta.",
    )
    if log:
        log("PASSO 5/9 — Scrittura ISO (operazione lunga)")
    write_iso(iso_path, disk, progress=progress, log=log)
    reread_partition_table(disk)

    step(
        82,
        "Fase 4 — Creazione partizioni per persistenza e dati utente\n"
        f"Nello spazio libero dopo l'ISO creo persistenza ext4 da {persist_mb} MB "
        "e home exFAT per i file personali.",
    )
    if log:
        log("PASSO 6/9 — Creazione partizioni")
    if not settle_usb_before_partition(disk, log) and log:
        log("ATTENZIONE: disco ancora con volumi aperti (Esplora risorse?).")
    persist_part, home_part = create_partitions_auto(disk, persist_mb, log, step=step)

    if log:
        log("PASSO 7/9 — Formattazione")
    format_partitions(persist_part, home_part, log, step=step)

    step(
        95,
        "Fase 6 — Preparazione cartelle e area export\n"
        "Creo le cartelle utente standard e «quelo-export» sulla partizione home exFAT.",
    )
    if log:
        log("PASSO 8/9 — Cartelle home exFAT")
    setup_home_folders(home_part, log)

    step(
        100,
        "Operazione completata con successo\n"
        "La chiavetta Quelo Office è pronta. Rimuovila in sicurezza e avvia il PC da USB.",
    )
    if log:
        log("PASSO 9/9 — Completato")
        log(lsblk_tree(disk))
