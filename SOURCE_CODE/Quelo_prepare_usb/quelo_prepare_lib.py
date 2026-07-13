#!/usr/bin/env python3
"""Libreria host per prepare-usb (CLI/GUI). NON per la live ISO."""

from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

QUELO_PUBLISH_ISO_VERSION = "0.71"
PERSIST_LABEL = "persistence"
HOME_LABEL = "QUELO-HOME"
PERSIST_SIZES_MB = (512, 1024, 2048)

ProgressCallback = Callable[[int, int, int, str], None]


class PrepareError(Exception):
    pass


@dataclass(frozen=True)
class DiskInfo:
    path: str
    name: str
    size: str
    model: str
    transport: str
    is_usb: bool


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


def is_live_session() -> bool:
    try:
        with open("/proc/cmdline", encoding="utf-8", errors="replace") as fh:
            if " boot=live" in fh.read():
                return True
    except OSError:
        pass
    for mp in ("/run/live/medium", "/lib/live/mount/medium"):
        if os.path.ismount(mp):
            return True
    return False


def require_host() -> None:
    if is_live_session():
        raise PrepareError(
            "Sei in sessione LIVE. Esegui dal PC host, non dalla USB avviata con Quelo Office."
        )


def require_root() -> None:
    if os.geteuid() != 0:
        raise PrepareError("Serve esecuzione come root (sudo o pkexec).")


def missing_dependencies() -> list[str]:
    need = {
        "wipefs": "util-linux",
        "fdisk": "util-linux",
        "mkfs.ext4": "e2fsprogs",
        "mkfs.exfat": "exfatprogs",
        "lsblk": "util-linux",
        "python3": "python3",
    }
    missing = []
    for cmd, pkg in need.items():
        if shutil.which(cmd) is None:
            missing.append(f"{cmd} ({pkg})")
    return missing


def part_suffix(disk: str) -> str:
    base = os.path.basename(disk)
    if base.startswith(("nvme", "mmcblk")):
        return "p"
    return ""


def part_paths(disk: str) -> tuple[str, str]:
    sfx = part_suffix(disk)
    return f"{disk}{sfx}3", f"{disk}{sfx}4"


def root_disk() -> str | None:
    try:
        out = subprocess.check_output(
            ["findmnt", "-no", "SOURCE", "/"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not out:
        return None
    try:
        pk = subprocess.check_output(
            ["lsblk", "-no", "PKNAME", out],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    return f"/dev/{pk}" if pk else None


def disk_is_usb(disk: str) -> bool:
    base = os.path.basename(disk)
    rem = f"/sys/block/{base}/removable"
    try:
        with open(rem, encoding="ascii") as fh:
            if fh.read().strip() == "1":
                return True
    except OSError:
        pass
    try:
        tran = subprocess.check_output(
            ["lsblk", "-dn", "-o", "TRAN", disk],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if tran == "usb":
            return True
    except subprocess.CalledProcessError:
        pass
    try:
        out = subprocess.check_output(
            ["udevadm", "info", "-q", "property", disk],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if line.startswith("ID_BUS=usb"):
                return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return False


def list_disks() -> list[DiskInfo]:
    try:
        raw = subprocess.check_output(
            ["lsblk", "-d", "-J", "-o", "NAME,SIZE,MODEL,TRAN,TYPE"],
            text=True,
        )
        data = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise PrepareError(f"Impossibile elencare i dischi: {exc}") from exc

    disks: list[DiskInfo] = []
    for block in data.get("blockdevices", []):
        if block.get("type") != "disk":
            continue
        name = block.get("name", "")
        path = name if name.startswith("/dev/") else f"/dev/{name}"
        disks.append(
            DiskInfo(
                path=path,
                name=os.path.basename(path),
                size=block.get("size", "?"),
                model=(block.get("model") or "").strip() or "—",
                transport=(block.get("tran") or "").strip() or "—",
                is_usb=disk_is_usb(path),
            )
        )
    return disks


def find_publish_iso() -> str | None:
    path = publish_iso_path()
    return path if os.path.isfile(path) else None


def human_size_bytes(size: int) -> str:
    val = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if val < 1024.0 or unit == "GiB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{size} B"


def human_size(path: str) -> str:
    try:
        return human_size_bytes(os.path.getsize(path))
    except OSError:
        return "?"


def lsblk_tree(disk: str) -> str:
    try:
        return subprocess.check_output(
            ["lsblk", "-o", "NAME,SIZE,FSTYPE,LABEL,TYPE,MOUNTPOINT", disk],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        return exc.output or str(exc)


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def umount_usb(disk: str, log: Callable[[str], None] | None = None) -> None:
    try:
        out = subprocess.check_output(
            ["lsblk", "-pln", "-o", "NAME,MOUNTPOINT", disk],
            text=True,
        )
    except subprocess.CalledProcessError:
        return
    prefix = disk
    for line in out.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        dev, mp = parts[0], parts[1]
        if not dev.startswith(prefix) or not mp:
            continue
        if log:
            log(f"Smonto {dev} ({mp})...")
        for cmd in (
            ["umount", dev],
            ["umount", "-l", dev],
            ["umount", mp],
            ["umount", "-l", mp],
        ):
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def swapoff_usb(disk: str) -> None:
    try:
        out = subprocess.check_output(
            ["lsblk", "-ln", "-o", "NAME,TYPE,FSTYPE", disk],
            text=True,
        )
    except subprocess.CalledProcessError:
        return
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "part" and parts[2] == "swap":
            subprocess.run(
                ["swapoff", f"/dev/{parts[0]}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def wipe_usb(disk: str, log: Callable[[str], None] | None = None) -> None:
    if log:
        log(f"Smonto tutte le partizioni su {disk}...")
    umount_usb(disk, log)
    swapoff_usb(disk)
    if log:
        log("Cancello firme e tabella partizioni (wipefs)...")
    _run(["wipefs", "-af", disk])
    subprocess.run(["sync"], check=False)
    subprocess.run(["partprobe", disk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["blockdev", "--rereadpt", disk],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if shutil.which("partx"):
        subprocess.run(["partx", "-d", disk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if log:
        log("Chiavetta azzerata: pronta per scrittura ISO.")


def settle_usb_before_partition(disk: str, log: Callable[[str], None] | None = None) -> bool:
    for attempt in range(5):
        umount_usb(disk, log)
        swapoff_usb(disk)
        time.sleep(1)
        try:
            out = subprocess.check_output(
                ["lsblk", "-ln", "-o", "NAME,MOUNTPOINT", disk],
                text=True,
            )
        except subprocess.CalledProcessError:
            return True
        mounted = any(
            len(line.split()) >= 2 and line.split()[1]
            for line in out.splitlines()
        )
        if not mounted:
            return True
        time.sleep(1)
    return False


def reread_partition_table(disk: str) -> None:
    subprocess.run(["partprobe", disk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["blockdev", "--rereadpt", disk],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)


def load_write_iso_module():
    path = os.path.join(script_dir(), "quelo-write-iso.py")
    spec = importlib.util.spec_from_file_location("quelo_write_iso", path)
    if spec is None or spec.loader is None:
        raise PrepareError("quelo-write-iso.py non trovato.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        cb(pct, current, total, elapsed, label)

    rc = mod.write_iso(
        iso_path,
        disk,
        use_direct=True,
        progress_callback=writer_progress,
    )
    if rc != 0:
        raise PrepareError("Scrittura ISO fallita.")
    if log:
        log("Verifica ISO in corso...")
    rc = mod.verify_iso(iso_path, disk, progress_callback=writer_progress)
    if rc != 0:
        raise PrepareError("Verifica ISO fallita.")
    subprocess.run(["sync"], check=False)


def verify_new_partitions(disk: str) -> tuple[str, str]:
    persist, home = part_paths(disk)
    if not os.path.exists(persist) or not os.path.exists(home):
        raise PrepareError(f"Partizioni mancanti: {persist} e/o {home}")
    try:
        size = int(
            subprocess.check_output(
                ["lsblk", "-bn", "-o", "SIZE", persist],
                text=True,
            ).strip()
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise PrepareError(f"Dimensione {persist} non valida.") from exc
    if size <= 0:
        raise PrepareError(f"Dimensione {persist} non valida ({size} byte).")
    return persist, home


def create_partitions_auto(
    disk: str,
    persist_mb: int,
    log: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    if not shutil.which("sfdisk"):
        raise PrepareError("sfdisk non trovato (util-linux).")

    script = f",{persist_mb}M,L\n,,L\n"
    for attempt in range(1, 4):
        if log:
            log(f"Creo partizioni con sfdisk (tentativo {attempt}/3)...")
        settle_usb_before_partition(disk, log)
        proc = subprocess.run(
            ["sfdisk", "--append", disk],
            input=script,
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            reread_partition_table(disk)
            try:
                return verify_new_partitions(disk)
            except PrepareError:
                if log:
                    log("Partizioni create ma verifica fallita, riprovo...")
        if proc.returncode != 0:
            if log:
                err = (proc.stderr or proc.stdout or "").strip()
                if err:
                    log(f"sfdisk: {err}")
                else:
                    log("sfdisk fallito.")
        time.sleep(2)

    raise PrepareError("Creazione automatica partizioni fallita dopo 3 tentativi.")


def format_partitions(
    persist_part: str,
    home_part: str,
    log: Callable[[str], None] | None = None,
) -> None:
    if log:
        log(f"Formatto {persist_part} (ext4, {PERSIST_LABEL})...")
    _run(["mkfs.ext4", "-F", "-L", PERSIST_LABEL, persist_part])
    if log:
        log(f"Formatto {home_part} (exFAT, {HOME_LABEL})...")
    _run(["mkfs.exfat", "-n", HOME_LABEL, home_part])


def setup_home_folders(home_part: str, log: Callable[[str], None] | None = None) -> None:
    home_mnt = tempfile.mkdtemp(prefix="quelo-home-")
    try:
        if log:
            log("Monto home exFAT...")
        _run(["mount", home_part, home_mnt])
        home_dir = os.path.join(home_mnt, "home")
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
        os.makedirs(os.path.join(home_mnt, "quelo-export"), exist_ok=True)
        with open(os.path.join(home_dir, ".quelo-prepared"), "w", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        subprocess.run(["sync"], check=False)
        if log:
            log("Cartelle home e quelo-export create.")
    finally:
        subprocess.run(["umount", home_mnt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.rmdir(home_mnt)
        except OSError:
            pass


def validate_confirm_text(disk_name: str, typed_name: str, typed_phrase: str) -> None:
    if typed_name != disk_name:
        raise PrepareError("Conferma 1/2 errata: nome disco non corrisponde.")
    if typed_phrase.strip().upper() != "SI SCRIVI":
        raise PrepareError("Conferma 2/2 errata: devi digitare SI SCRIVI.")


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
    if not os.path.exists(disk):
        raise PrepareError(f"Dispositivo non valido: {disk}")

    root = root_disk()
    if root and disk == root:
        raise PrepareError("Hai scelto il disco di sistema. STOP.")

    if persist_mb not in PERSIST_SIZES_MB:
        raise PrepareError(f"Dimensione persistenza non valida: {persist_mb} MB")

    if not disk_is_usb(disk) and not allow_non_usb:
        raise PrepareError("Il disco selezionato non risulta USB/removable.")

    if log:
        log("PASSO 4/9 — Pulizia chiavetta")
    wipe_usb(disk, log)

    if log:
        log("PASSO 5/9 — Scrittura ISO (operazione lunga)")
    write_iso(iso_path, disk, progress=progress, log=log)
    reread_partition_table(disk)

    if log:
        log("PASSO 6/9 — Creazione partizioni")
    if not settle_usb_before_partition(disk, log) and log:
        log("ATTENZIONE: disco ancora montato in parte (file manager?).")
    persist_part, home_part = create_partitions_auto(disk, persist_mb, log)

    if log:
        log("PASSO 7/9 — Formattazione")
    format_partitions(persist_part, home_part, log)

    if log:
        log("PASSO 8/9 — Cartelle home exFAT")
    setup_home_folders(home_part, log)

    if log:
        log("PASSO 9/9 — Completato")
        log(lsblk_tree(disk))
