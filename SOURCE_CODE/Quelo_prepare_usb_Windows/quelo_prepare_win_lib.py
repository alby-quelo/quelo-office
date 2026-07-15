#!/usr/bin/env python3
"""Libreria host prepare-usb per Windows 7+."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
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

# diskpart: comandi in inglese, output localizzato (IT/EN).
# Solo righe tabella "list partition" (evita falsi positivi nel testo libero).
_PARTITION_LINE_RE = re.compile(
    r"^\s*(?:Partition|Partizione)\s+(\d+)\s+\S",
    re.I | re.M,
)
_DRIVE_LETTER_RE = re.compile(
    r"(?:Letter|Lettera)\s*:\s*([A-Z]):",
    re.I,
)
_PARTITION_OFFSET_RE = re.compile(
    r"(?:Starting Offset|Offset di avvio|Offset in byte|Offset byte)\s*:\s*(\d+)",
    re.I,
)
_PARTITION_LIST_SIZE_RE = re.compile(
    r"^\s*(?:Partition|Partizione)\s+(\d+)\s+\S+\s+(\d+)\s*(KB|MB|GB|TB)\b",
    re.I | re.M,
)

SECTOR_SIZE = 512
_ALIGN_BYTES = 1024 * 1024  # 1 MiB
_GPT_SIGNATURE = b"EFI PART"
_GPT_TYPE_LINUX = uuid.UUID("0FC63DAF-8483-4772-8E79-3D69D8477DE4")
_GPT_TYPE_MSFT_BASIC = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")


def _emit_log(log: Callable[[str], None] | None, msg: str) -> None:
    """stderr (LOG-FULL via AVVIA.bat) + callback GUI."""
    print(msg, file=sys.stderr, flush=True)
    if log:
        log(msg)


def _win32_open_disk(disk_path: str, *, write: bool) -> int:
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    O_BINARY = getattr(os, "O_BINARY", 0)
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    access = GENERIC_READ | (GENERIC_WRITE if write else 0)
    handle = kernel32.CreateFileW(
        disk_path,
        access,
        0,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(kernel32.GetLastError(), "CreateFileW", disk_path)
    fd_flags = (os.O_RDWR if write else os.O_RDONLY) | O_BINARY
    return msvcrt.open_osfhandle(handle, fd_flags)


def _physical_read(disk_path: str, offset: int, size: int) -> bytes:
    fd = _win32_open_disk(disk_path, write=False)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        buf = b""
        while len(buf) < size:
            part = os.read(fd, size - len(buf))
            if not part:
                raise PrepareError(
                    f"Lettura disco incompleta @ {offset} (+{len(buf)}/{size} byte)."
                )
            buf += part
        return buf
    finally:
        os.close(fd)


def _physical_write(disk_path: str, offset: int, data: bytes) -> None:
    fd = _win32_open_disk(disk_path, write=True)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        os.write(fd, data)
        mod = load_write_iso_module()
        mod._win32_flush(fd)
    finally:
        os.close(fd)


def _gpt_crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _gpt_header_set_crc(header: bytearray) -> None:
    header[16:20] = b"\x00\x00\x00\x00"
    hdr_size = struct.unpack_from("<I", header, 12)[0]
    crc = _gpt_crc32(bytes(header[:hdr_size]))
    struct.pack_into("<I", header, 16, crc)


def _gpt_set_partition_array_crc(header: bytearray, entries: bytes) -> None:
    struct.pack_into("<I", header, 88, _gpt_crc32(entries))


@dataclass
class _GptLayout:
    header: bytearray
    entries: bytearray
    entry_lba: int
    num_entries: int
    entry_size: int
    backup_lba: int
    first_usable_lba: int
    last_usable_lba: int


def _gpt_load(disk_path: str, log: Callable[[str], None] | None = None) -> _GptLayout:
    """Legge header GPT (LBA 1) e array partizioni dal PhysicalDrive."""
    hdr_sector = _physical_read(disk_path, SECTOR_SIZE, SECTOR_SIZE)
    if hdr_sector[0:8] != _GPT_SIGNATURE:
        raise PrepareError(
            "Tabella GPT non trovata su LBA 1 dopo scrittura ISO.\n"
            "La chiavetta potrebbe non essere un'immagine GPT/ISO ibrida valida."
        )
    header = bytearray(hdr_sector)
    entry_lba = struct.unpack_from("<Q", header, 72)[0]
    num_entries = struct.unpack_from("<I", header, 80)[0]
    entry_size = struct.unpack_from("<I", header, 84)[0]
    backup_lba = struct.unpack_from("<Q", header, 32)[0]
    first_usable = struct.unpack_from("<Q", header, 40)[0]
    last_usable = struct.unpack_from("<Q", header, 48)[0]
    if num_entries <= 0 or entry_size < 128:
        raise PrepareError(f"GPT non valida: {num_entries} voci, size={entry_size}.")
    array_bytes = num_entries * entry_size
    array_sectors = (array_bytes + SECTOR_SIZE - 1) // SECTOR_SIZE
    raw = _physical_read(disk_path, entry_lba * SECTOR_SIZE, array_sectors * SECTOR_SIZE)
    entries = bytearray(raw[:array_bytes])
    if log:
        used = sum(
            1
            for i in range(num_entries)
            if entries[i * entry_size : i * entry_size + 16] != b"\x00" * 16
        )
        _emit_log(
            log,
            f"GPT letta: {used} partizioni attive, "
            f"usable LBA {first_usable}–{last_usable}, backup @ LBA {backup_lba}.",
        )
    return _GptLayout(
        header=header,
        entries=entries,
        entry_lba=entry_lba,
        num_entries=num_entries,
        entry_size=entry_size,
        backup_lba=backup_lba,
        first_usable_lba=first_usable,
        last_usable_lba=last_usable,
    )


def _gpt_partition_end_byte(entries: bytearray, index: int, entry_size: int) -> int | None:
    off = index * entry_size
    entry = entries[off : off + entry_size]
    if entry[0:16] == b"\x00" * 16:
        return None
    _first_lba, last_lba = struct.unpack_from("<QQ", entry, 32)
    if last_lba == 0:
        return None
    return (last_lba + 1) * SECTOR_SIZE


def _gpt_table_end_byte(layout: _GptLayout) -> int:
    end = 0
    for idx in range(layout.num_entries):
        part_end = _gpt_partition_end_byte(layout.entries, idx, layout.entry_size)
        if part_end is not None:
            end = max(end, part_end)
    return end


def _gpt_entry_name(entries: bytearray, index: int, entry_size: int) -> str:
    off = index * entry_size
    raw = entries[off + 56 : off + entry_size]
    nul = raw.find(b"\x00\x00")
    if nul >= 0:
        raw = raw[: nul + (nul % 2)]
    try:
        return raw.decode("utf-16le").strip("\x00")
    except UnicodeDecodeError:
        return ""


def _gpt_entry_offsets(entries: bytearray, index: int, entry_size: int) -> tuple[int, int] | None:
    off = index * entry_size
    entry = entries[off : off + entry_size]
    if entry[0:16] == b"\x00" * 16:
        return None
    first_lba, last_lba = struct.unpack_from("<QQ", entry, 32)
    if last_lba == 0:
        return None
    return first_lba, last_lba


def _gpt_iso_area_end_byte(layout: _GptLayout, iso_size_bytes: int) -> int:
    """Fine area ISO: ignora voci persist/home (retry) e protegge squashfs."""
    end = 0
    for idx in range(layout.num_entries):
        name = _gpt_entry_name(layout.entries, idx, layout.entry_size)
        if name in (PERSIST_LABEL, HOME_LABEL):
            continue
        part_end = _gpt_partition_end_byte(layout.entries, idx, layout.entry_size)
        if part_end is not None:
            end = max(end, part_end)
    return max(end, iso_size_bytes)


def _gpt_quelo_offsets(layout: _GptLayout) -> tuple[int, int] | None:
    persist_off = home_off = None
    for idx in range(layout.num_entries):
        name = _gpt_entry_name(layout.entries, idx, layout.entry_size)
        span = _gpt_entry_offsets(layout.entries, idx, layout.entry_size)
        if span is None:
            continue
        start_lba, _end_lba = span
        off_bytes = start_lba * SECTOR_SIZE
        if name == PERSIST_LABEL:
            persist_off = off_bytes
        elif name == HOME_LABEL:
            home_off = off_bytes
    if persist_off is not None and home_off is not None:
        return persist_off, home_off
    return None


def _gpt_entry_array_sectors(layout: _GptLayout) -> int:
    array_bytes = layout.num_entries * layout.entry_size
    return (array_bytes + SECTOR_SIZE - 1) // SECTOR_SIZE


def _disk_size_bytes(disk_number: int) -> int:
    """Dimensione reale del disco fisico (non la GPT embedded nell'ISO)."""
    ps = (
        f"$d = Get-Disk -Number {disk_number} -ErrorAction Stop; "
        "Write-Output $d.Size"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out.isdigit():
        raise PrepareError(
            f"Impossibile leggere dimensione disco {disk_number}.\n"
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )
    return int(out)


def _gpt_extend_to_physical_disk(
    layout: _GptLayout,
    physical_sectors: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """
    L'ISO hybrid porta una GPT sized ~1.3 GiB; su USB grandi estendiamo
    last_usable e backup GPT alla fine del disco fisico (come sfdisk --append).
    """
    array_sectors = _gpt_entry_array_sectors(layout)
    if physical_sectors <= array_sectors + 2:
        raise PrepareError("Disco fisico troppo piccolo per header GPT di backup.")

    new_backup_lba = physical_sectors - 1
    new_backup_entries_lba = new_backup_lba - array_sectors
    new_last_usable = new_backup_entries_lba - 1
    if new_last_usable <= layout.first_usable_lba:
        raise PrepareError("Spazio GPT insufficiente dopo estensione al disco fisico.")

    # Già estesa (resync post-format): non fallire.
    if layout.backup_lba == new_backup_lba and layout.last_usable_lba == new_last_usable:
        return

    if layout.backup_lba >= new_backup_lba and layout.last_usable_lba >= new_last_usable:
        return

    iso_disk_sectors = layout.backup_lba + 1
    if physical_sectors <= iso_disk_sectors and layout.backup_lba < new_backup_lba:
        raise PrepareError(
            f"USB troppo piccola: {human_size(physical_sectors * SECTOR_SIZE)} fisici, "
            f"serve spazio oltre l'ISO ({human_size(iso_disk_sectors * SECTOR_SIZE)})."
        )

    if log:
        _emit_log(
            log,
            f"Disco fisico: {human_size(physical_sectors * SECTOR_SIZE)} "
            f"({physical_sectors} settori); GPT ISO copre "
            f"{human_size(iso_disk_sectors * SECTOR_SIZE)} — estendo tabella.",
        )
        _emit_log(
            log,
            f"GPT estesa: last usable LBA {layout.last_usable_lba} → {new_last_usable}, "
            f"backup LBA {layout.backup_lba} → {new_backup_lba}.",
        )

    struct.pack_into("<Q", layout.header, 32, new_backup_lba)
    struct.pack_into("<Q", layout.header, 48, new_last_usable)
    layout.backup_lba = new_backup_lba
    layout.last_usable_lba = new_last_usable


def _probe_append_offset_after_iso(
    disk_path: str,
    disk_number: int,
    iso_size_bytes: int,
    persist_mb: int,
    log: Callable[[str], None] | None = None,
) -> int:
    """
    Dopo verify ISO: legge la chiavetta e calcola dove finisce l'area ISO
    (max tra byte ISO verificati e fine ultima voce GPT esistente).
    """
    _emit_log(log, f"Probe post-verify su {disk_path}...")
    layout = _gpt_load(disk_path, log)
    existing = _gpt_quelo_offsets(layout)
    if existing is not None:
        _emit_log(
            log,
            f"Voci GPT persist/home già presenti @ {human_size(existing[0])} "
            f"e {human_size(existing[1])} — skip append.",
        )
        return ((existing[0] + _ALIGN_BYTES - 1) // _ALIGN_BYTES) * _ALIGN_BYTES

    table_end = _gpt_iso_area_end_byte(layout, iso_size_bytes)
    _emit_log(
        log,
        f"ISO verificata: {human_size(iso_size_bytes)} ({iso_size_bytes} byte)",
    )
    _emit_log(log, f"Fine area ISO (GPT, senza persist/home): {human_size(table_end)}")
    part_only_end = 0
    for idx in range(layout.num_entries):
        name = _gpt_entry_name(layout.entries, idx, layout.entry_size)
        if name in (PERSIST_LABEL, HOME_LABEL):
            continue
        pe = _gpt_partition_end_byte(layout.entries, idx, layout.entry_size)
        if pe is not None:
            part_only_end = max(part_only_end, pe)
    if iso_size_bytes > part_only_end:
        _emit_log(
            log,
            "La ISO scritta supera la fine tabella GPT riportata: "
            "uso la dimensione ISO come limite minimo (protegge squashfs).",
        )
    end_bytes = table_end
    aligned = ((end_bytes + _ALIGN_BYTES - 1) // _ALIGN_BYTES) * _ALIGN_BYTES
    append_lba = aligned // SECTOR_SIZE

    physical_bytes = _disk_size_bytes(disk_number)
    physical_sectors = physical_bytes // SECTOR_SIZE
    array_sectors = _gpt_entry_array_sectors(layout)
    extended_last_usable = physical_sectors - 1 - array_sectors - 1
    persist_sectors = (persist_mb * 1024 * 1024) // SECTOR_SIZE
    min_home_sectors = 64 * 1024 * 1024 // SECTOR_SIZE  # almeno 64 MiB per home

    if append_lba + persist_sectors + min_home_sectors > extended_last_usable:
        raise PrepareError(
            f"Spazio insufficiente sulla USB: append @ {human_size(aligned)}, "
            f"persist {persist_mb} MB, disco fisico {human_size(physical_bytes)}."
        )

    _emit_log(
        log,
        f"Disco fisico: {human_size(physical_bytes)} — spazio utile dopo append: "
        f"{human_size((extended_last_usable - append_lba + 1) * SECTOR_SIZE)}.",
    )
    _emit_log(
        log,
        f"Offset append persist/home: {human_size(aligned)} ({aligned} byte, "
        f"{aligned // 1024} KB)",
    )
    return aligned


def _gpt_empty_slot_indices(layout: _GptLayout, need: int) -> list[int]:
    free: list[int] = []
    for idx in range(layout.num_entries):
        off = idx * layout.entry_size
        if layout.entries[off : off + 16] == b"\x00" * 16:
            free.append(idx)
        if len(free) >= need:
            break
    if len(free) < need:
        raise PrepareError(
            f"GPT piena: servono {need} slot liberi, trovati {len(free)}."
        )
    return free[:need]


def _gpt_write_entry(
    layout: _GptLayout,
    index: int,
    type_guid: uuid.UUID,
    first_lba: int,
    last_lba: int,
    name: str,
) -> None:
    off = index * layout.entry_size
    entry = bytearray(layout.entry_size)
    entry[0:16] = type_guid.bytes_le
    entry[16:32] = uuid.uuid4().bytes_le
    struct.pack_into("<Q", entry, 32, first_lba)
    struct.pack_into("<Q", entry, 40, last_lba)
    name_bytes = name[:36].encode("utf-16le")
    entry[56 : 56 + len(name_bytes)] = name_bytes
    layout.entries[off : off + layout.entry_size] = entry


def _gpt_commit(disk_path: str, layout: _GptLayout, log: Callable[[str], None] | None) -> None:
    array_bytes = layout.num_entries * layout.entry_size
    array_sectors = (array_bytes + SECTOR_SIZE - 1) // SECTOR_SIZE
    entries_raw = bytes(layout.entries[:array_bytes])
    if len(entries_raw) < array_sectors * SECTOR_SIZE:
        entries_raw = entries_raw.ljust(array_sectors * SECTOR_SIZE, b"\x00")

    backup_entries_lba = layout.backup_lba - array_sectors
    if backup_entries_lba <= layout.entry_lba:
        raise PrepareError(
            f"Spazio GPT backup insufficiente (entries LBA {backup_entries_lba})."
        )

    # Header primario (LBA 1): my_lba=1, alternate=backup, entries @ entry_lba.
    struct.pack_into("<Q", layout.header, 24, 1)
    struct.pack_into("<Q", layout.header, 32, layout.backup_lba)
    struct.pack_into("<Q", layout.header, 72, layout.entry_lba)
    _gpt_set_partition_array_crc(layout.header, entries_raw)
    _gpt_header_set_crc(layout.header)

    # Header backup (ultimo LBA): my_lba=backup, alternate=1, entries @ backup_entries_lba.
    # Senza partition_entry_lba corretto Linux/macOS ignorano le partizioni append.
    backup_header = bytearray(layout.header)
    struct.pack_into("<Q", backup_header, 24, layout.backup_lba)
    struct.pack_into("<Q", backup_header, 32, 1)
    struct.pack_into("<Q", backup_header, 72, backup_entries_lba)
    _gpt_header_set_crc(backup_header)

    _emit_log(
        log,
        "Scrivo voci GPT primarie e di backup (append, tabella ISO intatta)...",
    )
    _emit_log(
        log,
        f"GPT primary entries @ LBA {layout.entry_lba}, "
        f"backup header @ LBA {layout.backup_lba}, "
        f"backup entries @ LBA {backup_entries_lba}.",
    )
    _physical_write(disk_path, layout.entry_lba * SECTOR_SIZE, entries_raw)
    _physical_write(disk_path, SECTOR_SIZE, bytes(layout.header))
    _physical_write(disk_path, backup_entries_lba * SECTOR_SIZE, entries_raw)
    _physical_write(disk_path, layout.backup_lba * SECTOR_SIZE, bytes(backup_header))


def _gpt_resync_quelo_table(
    disk_path: str,
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Riscrive header GPT primario/backup dopo formattazione (validazione cross-OS)."""
    layout = _gpt_load(disk_path, log)
    if _gpt_quelo_offsets(layout) is None:
        raise PrepareError(
            "Resync GPT: voci «persistence» / «QUELO-HOME» non trovate in tabella."
        )
    physical_sectors = _disk_size_bytes(disk_number) // SECTOR_SIZE
    _gpt_extend_to_physical_disk(layout, physical_sectors, log)
    _emit_log(log, "Resync tabella GPT (header primario + backup alla fine disco)...")
    _gpt_commit(disk_path, layout, log)


# ISO hybrid (isohybrid): MBR NON-protettivo → Linux/Windows usano SOLO MBR.
# GPT è opaca per il kernel (niente 0xEE). Serve anche append MBR #3+#4
# (stesso effetto di: printf ',%sM,L\n,,7\n' | sfdisk --append).
_MBR_TYPE_LINUX = 0x83
_MBR_TYPE_EXFAT = 0x07  # HPFS/NTFS/exFAT — come tipo "7" di sfdisk


def _mbr_pack_entry(start_lba: int, num_sectors: int, part_type: int) -> bytes:
    if start_lba < 0 or num_sectors <= 0:
        raise PrepareError(f"Voce MBR non valida: start={start_lba} size={num_sectors}.")
    if start_lba > 0xFFFFFFFF or num_sectors > 0xFFFFFFFF:
        raise PrepareError(
            f"Partizione troppo grande per MBR (LBA {start_lba}+{num_sectors})."
        )
    entry = bytearray(16)
    entry[0] = 0x00  # non bootable
    # CHS start/end: 0xFEFFFF = “usa LBA” (come fdisk moderno)
    entry[1:4] = b"\xfe\xff\xff"
    entry[4] = part_type & 0xFF
    entry[5:8] = b"\xfe\xff\xff"
    struct.pack_into("<I", entry, 8, int(start_lba))
    struct.pack_into("<I", entry, 12, int(num_sectors))
    return bytes(entry)


def _mbr_append_two_partitions(
    disk_path: str,
    persist_first: int,
    persist_last: int,
    home_first: int,
    home_last: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """
    Scrive slot MBR 3 (Linux/ext4) e 4 (exFAT) senza toccare boot code né slot 1–2.
    Necessario: isohybrid → OS ignorano GPT e leggono solo MBR (FAI/syslinux).
    """
    mbr = bytearray(_physical_read(disk_path, 0, SECTOR_SIZE))
    if mbr[510:512] != b"\x55\xaa":
        raise PrepareError("MBR senza signature 0x55AA dopo scrittura ISO.")

    for slot in (0, 1):
        off = 446 + slot * 16
        if mbr[off : off + 16] == b"\x00" * 16:
            raise PrepareError(
                f"MBR hybrid incompleto: slot {slot + 1} vuoto "
                "(attesi ISO + EFI da isohybrid)."
            )

    persist_sectors = persist_last - persist_first + 1
    home_sectors = home_last - home_first + 1
    entry3 = _mbr_pack_entry(persist_first, persist_sectors, _MBR_TYPE_LINUX)
    entry4 = _mbr_pack_entry(home_first, home_sectors, _MBR_TYPE_EXFAT)
    mbr[446 + 2 * 16 : 446 + 3 * 16] = entry3
    mbr[446 + 3 * 16 : 446 + 4 * 16] = entry4

    _emit_log(
        log,
        f"Append MBR hybrid (slot 3–4, stile sfdisk --append): "
        f"persist type=0x83 LBA {persist_first}+{persist_sectors}, "
        f"home type=0x07 LBA {home_first}+{home_sectors}.",
    )
    _physical_write(disk_path, 0, bytes(mbr))


def _gpt_append_two_partitions(
    disk_path: str,
    disk_number: int,
    append_offset_bytes: int,
    persist_mb: int,
    log: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Aggiunge persist + home: GPT (UEFI) + MBR hybrid (Linux/Windows) = sfdisk --append."""
    layout = _gpt_load(disk_path, log)
    existing = _gpt_quelo_offsets(layout)
    if existing is not None:
        # Reinforza MBR anche se GPT già ok (retry / chiavetta semi-pronta).
        persist_first = persist_last = home_first = home_last = None
        for first_lba, last_lba, name in _gpt_list_partitions(layout):
            if name.lower() == PERSIST_LABEL.lower():
                persist_first, persist_last = first_lba, last_lba
            elif name == HOME_LABEL:
                home_first, home_last = first_lba, last_lba
        if None not in (persist_first, persist_last, home_first, home_last):
            try:
                _mbr_append_two_partitions(
                    disk_path,
                    persist_first,
                    persist_last,
                    home_first,
                    home_last,
                    log,
                )
            except PrepareError as exc:
                _emit_log(log, f"ATTENZIONE append MBR (voci GPT già presenti): {exc}")
        _emit_log(log, "Append GPT: voci persist/home già in tabella.")
        return existing

    physical_sectors = _disk_size_bytes(disk_number) // SECTOR_SIZE
    _gpt_extend_to_physical_disk(layout, physical_sectors, log)
    append_lba = append_offset_bytes // SECTOR_SIZE
    if append_lba < layout.first_usable_lba:
        raise PrepareError(
            f"Offset append LBA {append_lba} < first usable {layout.first_usable_lba}."
        )
    persist_lba_count = (persist_mb * 1024 * 1024) // SECTOR_SIZE
    if persist_lba_count <= 0:
        raise PrepareError(f"Dimensione persistenza non valida: {persist_mb} MB")

    persist_first = append_lba
    persist_last = persist_first + persist_lba_count - 1
    home_first = persist_last + 1
    home_last = layout.last_usable_lba
    if home_first > home_last:
        raise PrepareError(
            "Spazio insufficiente per home exFAT dopo la partizione persistenza."
        )

    slots = _gpt_empty_slot_indices(layout, 2)
    _gpt_write_entry(
        layout,
        slots[0],
        _GPT_TYPE_LINUX,
        persist_first,
        persist_last,
        PERSIST_LABEL,
    )
    _gpt_write_entry(
        layout,
        slots[1],
        _GPT_TYPE_MSFT_BASIC,
        home_first,
        home_last,
        HOME_LABEL,
    )
    _emit_log(
        log,
        f"Nuove voci GPT: persist LBA {persist_first}–{persist_last}, "
        f"home LBA {home_first}–{home_last}.",
    )
    _gpt_commit(disk_path, layout, log)
    # Critico: isohybrid → kernel/Windows vedono solo MBR (non la GPT).
    _mbr_append_two_partitions(
        disk_path, persist_first, persist_last, home_first, home_last, log
    )
    return persist_first * SECTOR_SIZE, home_first * SECTOR_SIZE


def _gpt_list_partitions(layout: _GptLayout) -> list[tuple[int, int, str]]:
    """Partizioni attive ordinate per LBA iniziale: (first_lba, last_lba, name)."""
    parts: list[tuple[int, int, str]] = []
    for idx in range(layout.num_entries):
        span = _gpt_entry_offsets(layout.entries, idx, layout.entry_size)
        if span is None:
            continue
        first_lba, last_lba = span
        name = _gpt_entry_name(layout.entries, idx, layout.entry_size)
        parts.append((first_lba, last_lba, name))
    parts.sort(key=lambda item: item[0])
    return parts


def _gpt_quelo_partition_numbers(
    layout: _GptLayout,
    log: Callable[[str], None] | None = None,
) -> tuple[int, int, int, int]:
    """
    Numeri partizione Windows (1-based, ordinati per LBA) per persist e home.
    Non usa diskpart: le ISO ibride spesso mostrano solo la partizione 1.
    """
    persist_num = home_num = 0
    persist_off = home_off = 0
    for number, (first_lba, _last_lba, name) in enumerate(
        _gpt_list_partitions(layout), start=1
    ):
        if name.lower() == PERSIST_LABEL.lower():
            persist_num = number
            persist_off = first_lba * SECTOR_SIZE
        elif name == HOME_LABEL:
            home_num = number
            home_off = first_lba * SECTOR_SIZE
    if not persist_num or not home_num:
        names = [n for _f, _l, n in _gpt_list_partitions(layout)]
        raise PrepareError(
            f"Voci GPT «{PERSIST_LABEL}» / «{HOME_LABEL}» non trovate.\n"
            f"Partizioni attive: {names or '(nessuna)'}"
        )
    _emit_log(
        log,
        f"Numeri partizione da GPT (ordine LBA): persist=#{persist_num}, home=#{home_num}",
    )
    return persist_num, home_num, persist_off, home_off


def _gpt_partition_size_bytes(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
) -> int | None:
    """Dimensione da tabella GPT (fallback se Windows non enumera ancora #3/#4)."""
    disk_path = physical_drive_path(disk_number)
    try:
        layout = _gpt_load(disk_path, log=None)
    except PrepareError:
        return None
    for number, (first_lba, last_lba, _name) in enumerate(
        _gpt_list_partitions(layout), start=1
    ):
        if number != partition_number:
            continue
        size = (last_lba - first_lba + 1) * SECTOR_SIZE
        if log:
            log(
                f"Partizione {partition_number} dimensione {human_size(size)} "
                f"({size} byte, da GPT — Windows non l'ha ancora enumerata)"
            )
        return size
    return None


def _ioctl_disk_update_properties(disk_path: str) -> None:
    import msvcrt
    from ctypes import byref, c_ulong, windll

    IOCTL_DISK_UPDATE_PROPERTIES = 0x00000050
    kernel32 = windll.kernel32
    fd = _win32_open_disk(disk_path, write=False)
    try:
        handle = msvcrt.get_osfhandle(fd)
        returned = c_ulong(0)
        kernel32.DeviceIoControl(
            handle,
            IOCTL_DISK_UPDATE_PROPERTIES,
            None,
            0,
            None,
            0,
            byref(returned),
            None,
        )
    finally:
        os.close(fd)


def _rescan_disk_aggressive(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Forza Windows a rileggere la GPT dopo scrittura raw."""
    disk_path = physical_drive_path(disk_number)
    _release_usb_volumes(disk_number, log)
    _diskpart_try([f"select disk {disk_number}", "online disk"], log, "online disk")
    _diskpart_try([f"select disk {disk_number}", "rescan"], log, "rescan")
    subprocess.run(["mountvol", "/E"], capture_output=True)
    ps = (
        f"$dn = {disk_number}; "
        "Update-Disk -Number $dn -ErrorAction SilentlyContinue | Out-Null; "
        "Get-Disk -Number $dn -ErrorAction SilentlyContinue | Update-Disk | Out-Null"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    try:
        _ioctl_disk_update_properties(disk_path)
    except OSError as exc:
        if log:
            _emit_log(log, f"ATTENZIONE IOCTL update properties: {exc}")
    time.sleep(3)


def _enumerate_disk_partitions(
    disk_number: int,
) -> list[tuple[int, int, int]]:
    """(numero_partizione, offset_byte, size_byte) da WMI + diskpart."""
    found: dict[int, tuple[int, int]] = {}

    ps = (
        f"$dn = {disk_number}; "
        "Get-CimInstance Win32_DiskPartition | Where-Object {{ $_.DiskIndex -eq $dn }} | "
        "ForEach-Object {{ "
        "Write-Output ($_.Index.ToString() + ':' + $_.StartingAddress.ToString() + ':' + $_.Size.ToString()) "
        "}}"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        pnum, off, size = int(parts[0]), int(parts[1]), int(parts[2])
        found[pnum] = (off, size)

    ps_gp = (
        f"$dn = {disk_number}; "
        "Get-Partition -DiskNumber $dn -ErrorAction SilentlyContinue | ForEach-Object { "
        "Write-Output ($_.PartitionNumber.ToString() + ':' + $_.StartingOffset.ToString() + ':' + $_.Size.ToString()) "
        "}"
    )
    proc_gp = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_gp],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    for line in (proc_gp.stdout or "").splitlines():
        parts = line.strip().split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        pnum, off, size = int(parts[0]), int(parts[1]), int(parts[2])
        found[pnum] = (off, size)

    for pnum in _list_partition_numbers(disk_number):
        try:
            off = _partition_offset_bytes(disk_number, pnum, None)
        except PrepareError:
            continue
        sizes = _partition_sizes_bytes_diskpart(disk_number)
        size = sizes.get(pnum, 0)
        if size <= 0:
            ps2 = (
                f"$p = Get-Partition -DiskNumber {disk_number} -PartitionNumber {pnum} "
                "-ErrorAction SilentlyContinue; "
                "if ($p) { Write-Output $p.Size }"
            )
            proc2 = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps2],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
            )
            out2 = (proc2.stdout or "").strip()
            size = int(out2) if out2.isdigit() else 0
        prev = found.get(pnum, (off, 0))
        found[pnum] = (off, size if size > 0 else prev[1])

    return sorted((pnum, off, size) for pnum, (off, size) in found.items())


def _resolve_partition_at_offset(
    disk_number: int,
    offset_bytes: int,
    log: Callable[[str], None] | None = None,
    *,
    size_bytes: int | None = None,
    tolerance: int = _ALIGN_BYTES,
) -> int:
    """Trova numero partizione Windows per offset GPT (tolleranza 1 MiB)."""
    for attempt in range(1, 11):
        if attempt > 1:
            _rescan_disk_aggressive(disk_number, log)
        parts = _enumerate_disk_partitions(disk_number)
        if log:
            _emit_log(
                log,
                f"Ricerca partizione @ {human_size(offset_bytes)} "
                f"(tentativo {attempt}/10, viste: "
                f"{[(p, human_size(o)) for p, o, _s in parts]})",
            )
        best_num = 0
        best_delta = tolerance + 1
        for pnum, off, size in parts:
            delta = abs(off - offset_bytes)
            if delta <= tolerance and delta < best_delta:
                if size_bytes is not None and size > 0:
                    size_delta = abs(size - size_bytes)
                    if size_delta > max(size_bytes // 20, _ALIGN_BYTES):
                        continue
                best_num = pnum
                best_delta = delta
        if best_num:
            _emit_log(
                log,
                f"Partizione #{best_num} @ {human_size(offset_bytes)} "
                f"(delta {best_delta} B)",
            )
            return best_num
        time.sleep(2)
    raise PrepareError(
        f"Partizione @ offset {human_size(offset_bytes)} non trovata dopo rescan.\n"
        "Windows non ha enumerato le voci GPT append — riprova o scollega/ricollega la USB."
    )


def _partition_number_at_offset(
    disk_number: int,
    offset_bytes: int,
    log: Callable[[str], None] | None = None,
    *,
    size_bytes: int | None = None,
) -> int:
    return _resolve_partition_at_offset(
        disk_number, offset_bytes, log, size_bytes=size_bytes
    )


def _assert_offsets_safe(
    disk_path: str,
    iso_size_bytes: int,
    min_offset_bytes: int,
    persist_offset: int,
    home_offset: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Doppio controllo: GPT sul disco + soglia ISO prima di formattare."""
    layout = _gpt_load(disk_path, log=None)
    for label, off in (("persistenza", persist_offset), ("home", home_offset)):
        if off < min_offset_bytes:
            raise PrepareError(
                f"Partizione {label} @ {human_size(off)} "
                f"< offset minimo {human_size(min_offset_bytes)}."
            )
        if off < iso_size_bytes:
            raise PrepareError(
                f"Partizione {label} @ {human_size(off)} "
                f"dentro l'ISO ({human_size(iso_size_bytes)}): squashfs a rischio."
            )
    # Verifica indipendente sulla GPT appena scritta
    for idx in range(layout.num_entries):
        off = idx * layout.entry_size
        entry = layout.entries[off : off + layout.entry_size]
        if entry[0:16] == b"\x00" * 16:
            continue
        first_lba, last_lba = struct.unpack_from("<QQ", entry, 32)
        start = first_lba * SECTOR_SIZE
        if start not in (persist_offset, home_offset):
            continue
        if start < iso_size_bytes:
            raise PrepareError(
                f"Voce GPT #{idx + 1} @ {human_size(start)} sovrappone l'ISO."
            )
    if log:
        _emit_log(
            log,
            f"Validazione offset OK: persist/home >= {human_size(min_offset_bytes)} "
            f"e >= ISO {human_size(iso_size_bytes)}.",
        )

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


def _gptfdisk_dir() -> str:
    return os.path.join(script_dir(), "windows", "tools", "gptfdisk")


def _sgdisk_exe() -> str:
    """GPT fdisk — solo sgdisk32 (PE32, Win32 nativo e WoW64 su Win64)."""
    tools = _gptfdisk_dir()
    candidate = os.path.join(tools, "sgdisk32.exe")
    if os.path.isfile(candidate):
        return candidate
    found = shutil.which("sgdisk32")
    if found:
        return found
    raise PrepareError(
        "sgdisk32.exe non trovato.\n"
        "Manca windows\\tools\\gptfdisk\\ (rigenera lo zip con build-archives.sh)."
    )


_SGDISK_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+"
    r"(\d+(?:\.\d+)?\s(?:KiB|MiB|GiB|TiB|PiB))\s+"
    r"(\S+)\s+(.*)\s*$"
)


@dataclass
class _SgdiskPart:
    number: int
    start_sector: int
    end_sector: int
    size: str
    code: str
    name: str

    @property
    def offset_bytes(self) -> int:
        return self.start_sector * SECTOR_SIZE


def _sgdisk_run(
    args: list[str],
    disk_path: str,
    log: Callable[[str], None] | None = None,
    *,
    timeout: int = 120,
) -> str:
    sgdisk = _sgdisk_exe()
    cmd = [sgdisk, *args, disk_path]
    run_flags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_flags = subprocess.CREATE_NO_WINDOW
    _emit_log(log, f"sgdisk {' '.join(args)} {disk_path}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=run_flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrepareError(f"sgdisk scaduto dopo {timeout}s.") from exc
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise PrepareError(f"sgdisk fallito ({' '.join(cmd)}):\n{out}")
    if out and log:
        for line in out.splitlines():
            _emit_log(log, line)
    return out


def _sgdisk_parse_table(output: str) -> list[_SgdiskPart]:
    parts: list[_SgdiskPart] = []
    in_table = False
    for line in output.splitlines():
        if re.match(r"^\s*Number\s+Start\s+\(sector\)", line, re.I):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.strip():
            break
        m = _SGDISK_ROW_RE.match(line)
        if not m:
            continue
        parts.append(
            _SgdiskPart(
                number=int(m.group(1)),
                start_sector=int(m.group(2)),
                end_sector=int(m.group(3)),
                size=m.group(4).strip(),
                code=m.group(5),
                name=m.group(6).strip(),
            )
        )
    return parts


def _sgdisk_find_quelo(
    parts: list[_SgdiskPart],
) -> tuple[_SgdiskPart | None, _SgdiskPart | None]:
    persist: _SgdiskPart | None = None
    home: _SgdiskPart | None = None
    for part in parts:
        if part.name.lower() == PERSIST_LABEL.lower():
            persist = part
        elif part.name == HOME_LABEL:
            home = part
    return persist, home


def _sgdisk_assert_safe(
    persist: _SgdiskPart,
    iso_size_bytes: int,
    log: Callable[[str], None] | None = None,
) -> None:
    if persist.offset_bytes < iso_size_bytes:
        raise PrepareError(
            f"BLOCCO sicurezza: persist @ {human_size(persist.offset_bytes)} "
            f"< ISO {human_size(iso_size_bytes)} — non formattare."
        )
    _emit_log(
        log,
        f"Offset persist verificato: {human_size(persist.offset_bytes)} "
        f"(>= ISO {human_size(iso_size_bytes)}).",
    )


def _mke2fs_exe() -> str:
    bundled = os.path.join(_tools_dir(), "mke2fs.exe")
    if os.path.isfile(bundled):
        return bundled
    for candidate in (
        r"C:\cygwin\bin\mke2fs.exe",
        r"C:\cygwin64\bin\mke2fs.exe",
    ):
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("mke2fs")
    if found:
        return found
    raise PrepareError(
        "mke2fs.exe non trovato.\n"
        "Esegui AVVIA.bat oppure vedi LEGGIMI-WINDOWS.txt"
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


def _run_capture(cmd: list[str], *, input_text: str | None = None, timeout: int = 20) -> str:
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrepareError(
            f"Comando scaduto dopo {timeout}s ({' '.join(cmd)}).\n"
            "Riprova o attendi (su VM puo essere lento)."
        ) from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise PrepareError(f"Comando fallito ({' '.join(cmd)}):\n{out.strip()}")
    return out


def _run_diskpart(lines: list[str], *, timeout: int = 60) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="ascii") as fh:
        fh.write("\n".join(lines))
        fh.write("\n")
        script = fh.name
    try:
        return _run_capture(["diskpart", "/s", script], timeout=timeout)
    except PrepareError as exc:
        script_body = "\n".join(lines)
        raise PrepareError(f"{exc}\nScript diskpart:\n{script_body}") from exc
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def _diskpart_try(
    lines: list[str],
    log: Callable[[str], None] | None = None,
    label: str = "",
    *,
    timeout: int = 60,
) -> bool:
    try:
        _run_diskpart(lines, timeout=timeout)
        return True
    except PrepareError as exc:
        if log:
            tag = f"{label}: " if label else ""
            log(f"ATTENZIONE {tag}{exc}")
        return False


def _ps_run_flags() -> int:
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0


def _powershell(
    script: str,
    *,
    timeout: int = 60,
    log: Callable[[str], None] | None = None,
    label: str = "",
    fatal: bool = False,
) -> bool:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_ps_run_flags(),
        )
    except subprocess.TimeoutExpired:
        msg = f"PowerShell scaduto ({label or 'script'}) dopo {timeout}s."
        if fatal:
            raise PrepareError(msg)
        if log:
            log(f"ATTENZIONE {msg}")
        return False
    if proc.returncode == 0:
        return True
    err = (proc.stderr or proc.stdout or "").strip()
    if fatal:
        raise PrepareError(f"PowerShell fallito ({label}):\n{err}")
    if log and err:
        tag = f"{label}: " if label else ""
        log(f"ATTENZIONE {tag}{err}")
    return False


def _partition_drive_letter(disk_number: int, partition_number: int) -> str | None:
    ps = (
        f"$p = Get-Partition -DiskNumber {disk_number} -PartitionNumber {partition_number} "
        f"-ErrorAction SilentlyContinue; "
        "if ($p -and $p.DriveLetter) { Write-Output $p.DriveLetter }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=_ps_run_flags(),
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    letter = (proc.stdout or "").strip()
    if len(letter) == 1 and letter.isalpha():
        return letter.upper()
    return None


_VOLUME_FS_RE = re.compile(
    r"\b(NTFS|exFAT|FAT32|FAT16|FAT|RAW|UDF|ReFS|CDFS)\b",
    re.I,
)


def _powershell_query(
    script: str,
    *,
    timeout: int = 60,
) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_ps_run_flags(),
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or proc.stdout or "").strip()
    if proc.returncode != 0:
        raise PrepareError(err or f"PowerShell exit {proc.returncode}")
    return out


def _volume_drive_letter(label: str) -> str | None:
    ps = (
        f"$v = Get-Volume -FileSystemLabel '{label}' -ErrorAction SilentlyContinue "
        f"| Select-Object -First 1; "
        "if ($v -and $v.DriveLetter) { Write-Output $v.DriveLetter }"
    )
    try:
        letter = _powershell_query(ps, timeout=20).strip().upper()
    except PrepareError:
        return None
    if len(letter) == 1 and letter.isalpha():
        return letter
    return None


def _volume_label_for_letter(letter: str) -> str:
    letter = letter.strip().upper()
    if len(letter) != 1 or not letter.isalpha():
        return ""
    ps = (
        f"$v = Get-Volume -DriveLetter '{letter}' -ErrorAction SilentlyContinue; "
        "if ($v) { Write-Output $v.FileSystemLabel }"
    )
    try:
        return _powershell_query(ps, timeout=15).strip()
    except PrepareError:
        return ""


def _diskpart_volume_by_label(label: str) -> tuple[int, str | None] | None:
    try:
        out = _run_diskpart(["list volume"])
    except PrepareError:
        return None
    want = label.upper()
    for line in out.splitlines():
        m = re.match(r"^\s*Volume\s+(\d+)\s+(.+)$", line, re.I)
        if not m:
            continue
        vol_num = int(m.group(1))
        tokens = m.group(2).split()
        fs_idx = next(
            (i for i, tok in enumerate(tokens) if _VOLUME_FS_RE.match(tok)),
            None,
        )
        if fs_idx is None or fs_idx == 0:
            continue
        letter = None
        if fs_idx >= 2 and len(tokens[0]) == 1 and tokens[0].isalpha():
            letter = tokens[0].upper()
            vol_label = " ".join(tokens[1:fs_idx])
        else:
            vol_label = " ".join(tokens[:fs_idx])
        if vol_label.upper() == want:
            return vol_num, letter
    return None


_HOME_LETTER_PREFERENCE = "TUVWXYZQRPONMLKJIHGF"


def _pick_home_letter() -> str:
    for letter in _HOME_LETTER_PREFERENCE:
        if letter == "C":
            continue
        if not os.path.exists(f"{letter}:\\"):
            return letter
    raise PrepareError("Nessuna lettera di unità libera (preferenza T/U/...).")


def _free_drive_letter() -> str:
    return _pick_home_letter()


def _prepare_volume_discovery(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    subprocess.run(
        ["mountvol", "/E"],
        capture_output=True,
        creationflags=_ps_run_flags(),
    )
    _diskpart_try(
        [f"select disk {disk_number}", "online disk", "rescan"],
        log,
        "rescan pre-montaggio home",
    )
    _powershell(
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"Update-Disk -Number {disk_number} | Out-Null; "
        f"Get-Disk -Number {disk_number} | Update-Disk | Out-Null",
        log=log,
        label="Update-Disk home",
    )
    subprocess.run(["mountvol", "/E"], capture_output=True, creationflags=_ps_run_flags())


def _parse_volume_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "---":
            continue
        record: dict[str, str] = {}
        for part in line.split("|"):
            if "=" not in part:
                continue
            key, _, value = part.partition("=")
            record[key.strip().upper()] = value.strip()
        if record:
            records.append(record)

    proc = subprocess.run(
        ["mountvol"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_ps_run_flags(),
    )
    orphan_paths: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("\\\\?\\Volume{") and line.endswith("}"):
            orphan_paths.append(line + "\\")
        elif line.startswith("\\\\?\\Volume{") and line.endswith("}\\"):
            orphan_paths.append(line)
    for path in orphan_paths:
        records.append(
            {
                "SRC": "MountVol",
                "PATH": path,
                "LETTER": "",
                "LABEL": "",
                "SIZE": "",
            }
        )
    return records


def _find_home_volume_candidates(
    disk_number: int,
    home_size_bytes: int | None = None,
) -> list[dict[str, str]]:
    min_size = max((home_size_bytes or 0) - 512 * 1024 * 1024, 5 * 1024 * 1024 * 1024)
    ps = (
        f"$label='{HOME_LABEL}'; $minSize=[int64]{min_size}; "
        f"$dn={disk_number}; "
        "Get-Volume -FileSystemLabel $label -ErrorAction SilentlyContinue "
        "| ForEach-Object { "
        "  Write-Output ('SRC=GetVolume|PATH=' + $_.Path + '|LETTER=' + $_.DriveLetter "
        "    + '|LABEL=' + $_.FileSystemLabel + '|SIZE=' + $_.Size); Write-Output '---' "
        "}; "
        "Get-CimInstance Win32_Volume -ErrorAction SilentlyContinue | Where-Object { "
        "  ($_.Label -eq $label) -or "
        "  ($_.FileSystem -eq 'exFAT' -and [string]::IsNullOrEmpty($_.DriveLetter) "
        "   -and [uint64]$_.Capacity -ge $minSize) "
        "} | ForEach-Object { "
        "  $dl = if ($_.DriveLetter) { $_.DriveLetter } else { '' }; "
        "  Write-Output ('SRC=Win32|PATH=' + $_.DeviceID + '|LETTER=' + $dl "
        "    + '|LABEL=' + $_.Label + '|SIZE=' + $_.Capacity); Write-Output '---' "
        "}; "
        "Get-Volume -FileSystem exFAT -ErrorAction SilentlyContinue "
        "| Where-Object { -not $_.DriveLetter -and $_.Size -ge $minSize } "
        "| ForEach-Object { "
        "  Write-Output ('SRC=ExFAT|PATH=' + $_.Path + '|LETTER=|LABEL=' "
        "    + $_.FileSystemLabel + '|SIZE=' + $_.Size); Write-Output '---' "
        "}"
    )
    try:
        out = _powershell_query(ps, timeout=45)
    except PrepareError:
        out = ""
    records = _parse_volume_records(out)

    try:
        dp_out = _run_diskpart(["list volume"])
    except PrepareError:
        dp_out = ""
    for line in dp_out.splitlines():
        m = re.match(r"^\s*Volume\s+(\d+)\s+(.+)$", line, re.I)
        if not m:
            continue
        vol_num = m.group(1)
        tokens = m.group(2).split()
        fs_idx = next(
            (i for i, tok in enumerate(tokens) if _VOLUME_FS_RE.match(tok)),
            None,
        )
        if fs_idx is None:
            continue
        fs = tokens[fs_idx].upper()
        if fs != "EXFAT":
            continue
        letter = ""
        if fs_idx >= 2 and len(tokens[0]) == 1 and tokens[0].isalpha():
            letter = tokens[0].upper()
            vol_label = " ".join(tokens[1:fs_idx])
        else:
            vol_label = " ".join(tokens[:fs_idx])
        records.append(
            {
                "SRC": "DiskPart",
                "VOLNUM": vol_num,
                "PATH": "",
                "LETTER": letter,
                "LABEL": vol_label,
                "SIZE": "",
            }
        )

    proc = subprocess.run(
        ["mountvol"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_ps_run_flags(),
    )
    block = (proc.stdout or "").split("*** NO MOUNT POINTS ***")
    for chunk in block:
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("\\\\?\\Volume{") and line.endswith("}"):
                records.append(
                    {
                        "SRC": "MountVol",
                        "PATH": line + "\\",
                        "LETTER": "",
                        "LABEL": "",
                        "SIZE": "",
                    }
                )
            elif line.startswith("\\\\?\\Volume{") and line.endswith("}\\"):
                records.append(
                    {
                        "SRC": "MountVol",
                        "PATH": line,
                        "LETTER": "",
                        "LABEL": "",
                        "SIZE": "",
                    }
                )
    return records


def _mountvol_assign(letter: str, volume_path: str) -> bool:
    letter = letter.upper()
    path = volume_path.strip()
    if path and not path.endswith("\\"):
        path += "\\"
    if not path:
        return False
    proc = subprocess.run(
        ["mountvol", f"{letter}:", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_ps_run_flags(),
    )
    return proc.returncode == 0 and os.path.isdir(f"{letter}:\\")


def _home_volume_ready(letter: str) -> bool:
    return os.path.isdir(os.path.join(f"{letter}:\\", "home"))


def _mount_home_exfat_volume(
    disk_number: int,
    log: Callable[[str], None] | None = None,
    *,
    home_size_bytes: int | None = None,
    home_partition_number: int | None = None,
) -> str:
    """
    Monta QUELO-HOME in Esplora file via etichetta volume exFAT.
    Dopo append GPT + FATtools, Windows vede il volume ma non la partizione #4.
    """
    disk_path = physical_drive_path(disk_number)
    try:
        _gpt_resync_quelo_table(disk_path, disk_number, log)
    except PrepareError as exc:
        if log:
            _emit_log(log, f"ATTENZIONE resync GPT pre-montaggio: {exc}")

    letter = _volume_drive_letter(HOME_LABEL)
    if letter and os.path.isdir(f"{letter}:\\"):
        return letter

    preferred = _pick_home_letter()
    if log:
        log(
            f"Assegno lettera {preferred}: a «{HOME_LABEL}» "
            f"(attendo riconoscimento volume Windows)..."
        )

    part_n = home_partition_number
    if part_n is None:
        try:
            layout = _gpt_load(disk_path, log=None)
            _pnum, part_n, _poff, _hoff = _gpt_quelo_partition_numbers(layout, log=None)
        except PrepareError:
            part_n = None

    for attempt in range(1, 21):
        if attempt == 1 or attempt % 4 == 0:
            _rescan_disk_aggressive(disk_number, log if attempt == 1 else None)
        else:
            subprocess.run(
                ["mountvol", "/E"],
                capture_output=True,
                creationflags=_ps_run_flags(),
            )
        time.sleep(1 if attempt <= 3 else 2)

        letter = _volume_drive_letter(HOME_LABEL)
        if letter and os.path.isdir(f"{letter}:\\"):
            return letter

        if part_n and _diskpart_try(
            [
                f"select disk {disk_number}",
                f"select partition {part_n}",
                f"assign letter={preferred}",
            ],
            log if attempt <= 2 else None,
            f"assign partition {part_n} → {preferred}",
        ) and _home_volume_ready(preferred):
            if log:
                log(f"Partizione #{part_n} → {preferred}: (diskpart assign)")
            return preferred

        candidates = _find_home_volume_candidates(disk_number, home_size_bytes)
        if log and candidates:
            _emit_log(
                log,
                f"Volume candidati (tentativo {attempt}/20): "
                f"{[(c.get('SRC'), c.get('LABEL'), c.get('LETTER')) for c in candidates]}",
            )

        for cand in candidates:
            if cand.get("SRC") == "MountVol" and not (cand.get("LABEL") or "").strip():
                continue

            existing = (cand.get("LETTER") or "").strip().upper()
            if len(existing) == 1 and existing.isalpha() and os.path.isdir(f"{existing}:\\"):
                return existing

            letter = preferred
            if os.path.exists(f"{letter}:\\"):
                letter = _pick_home_letter()

            vol_path = (cand.get("PATH") or "").strip()
            if vol_path and _mountvol_assign(letter, vol_path):
                if _home_volume_ready(letter):
                    if log:
                        log(f"mountvol {letter}: ← {vol_path} ({cand.get('SRC')})")
                    return letter
                _dismount_letter(letter)

            vol_num = (cand.get("VOLNUM") or "").strip()
            if vol_num.isdigit() and _diskpart_try(
                [f"select volume={vol_num}", f"assign letter={letter}"],
                log,
                f"assign volume {vol_num} → {letter}",
            ) and _home_volume_ready(letter):
                return letter
            if vol_num.isdigit() and os.path.exists(f"{letter}:\\"):
                _dismount_letter(letter)

        if log and attempt % 5 == 0:
            log(f"Ancora in attesa del volume «{HOME_LABEL}» ({attempt}/20)...")

    raise PrepareError(
        f"Impossibile montare «{HOME_LABEL}» in Esplora file.\n"
        f"Provato ad assegnare {preferred}: — Windows non ha esposto il volume exFAT.\n"
        "Prova: Gestione disco → trova QUELO-HOME o exFAT → Assegna lettera T: o U:."
    )


def _assign_partition_letter(
    disk_number: int,
    partition_number: int,
    letter: str,
    log: Callable[[str], None] | None = None,
) -> None:
    letter = letter.upper()
    ps = (
        f"$ErrorActionPreference='Stop'; "
        f"$dn={disk_number}; $pn={partition_number}; $L='{letter}'; "
        "$p = Get-Partition -DiskNumber $dn -PartitionNumber $pn -ErrorAction Stop; "
        "if ($p.DriveLetter) { "
        "  if ($p.DriveLetter -eq $L) { exit 0 }; "
        "  Remove-PartitionAccessPath -DiskNumber $dn -PartitionNumber $pn "
        "    -AccessPath ($p.DriveLetter + ':\\') -ErrorAction SilentlyContinue "
        "}; "
        "Set-Partition -DiskNumber $dn -PartitionNumber $pn -NewDriveLetter $L"
    )
    if _powershell(ps, log=log, label=f"assign {letter} PS"):
        return
    _run_diskpart(
        [
            f"select disk {disk_number}",
            f"select partition {partition_number}",
            f"assign letter={letter}",
        ]
    )


def _remove_partition_letter(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    letter = _partition_drive_letter(disk_number, partition_number)
    if not letter:
        return
    _dismount_letter(letter)
    ps = (
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"Remove-PartitionAccessPath -DiskNumber {disk_number} -PartitionNumber "
        f"{partition_number} -AccessPath '{letter}:\\'"
    )
    if _powershell(ps, log=log, label=f"remove {letter} PS"):
        return
    _diskpart_try(
        [
            f"select disk {disk_number}",
            f"select partition {partition_number}",
            f"remove letter={letter}",
        ],
        log,
        f"remove letter {letter}",
    )


def _set_partition_hidden(
    disk_number: int,
    partition_number: int,
    hidden: bool,
    log: Callable[[str], None] | None = None,
) -> None:
    flag = "$true" if hidden else "$false"
    ps = (
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"Set-Partition -DiskNumber {disk_number} -PartitionNumber {partition_number} "
        f"-IsHidden {flag}"
    )
    _powershell(ps, log=log, label=f"IsHidden={hidden}")


def _ensure_partition_letter(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
) -> str:
    letter = _partition_drive_letter(disk_number, partition_number)
    if letter:
        if log:
            log(f"Partizione {partition_number} già montata come {letter}:")
        return letter
    letter = _free_drive_letter()
    _assign_partition_letter(disk_number, partition_number, letter, log)
    return letter


def _diskpart_list_disk_output() -> str:
    return _run_diskpart(["list disk"])


def _parse_disk_list_line(line: str) -> tuple[int, str] | None:
    """Estrae (numero, size) da riga 'list disk' (IT/EN)."""
    m = re.match(
        r"^\s*(?:Disk|Disco)\s+(\d+)\s+\S+\s+(.+?)\s*(?:\*.*)?$",
        line,
        re.I,
    )
    if not m:
        return None
    idx = int(m.group(1))
    size_part = m.group(2).strip()
    size_m = re.match(r"([\d.,]+\s*(?:GB|MB|KB|B|TB))", size_part, re.I)
    size = size_m.group(1).strip() if size_m else size_part.split()[0]
    return idx, size


def _disk_detail(disk_number: int) -> str:
    return _run_diskpart([f"select disk {disk_number}", "detail disk"])


def _disk_model_and_usb(detail: str) -> tuple[str, bool]:
    model = "—"
    for pattern in (
        r"^\s*(?:Modello?|Model)\s*:\s*(.+)$",
        r"^\s*(?:Friendly Name|Nome descrittivo)\s*:\s*(.+)$",
    ):
        m = re.search(pattern, detail, re.I | re.M)
        if m:
            model = m.group(1).strip()
            break
    if model == "—":
        first = detail.strip().splitlines()[0].strip() if detail.strip() else ""
        if first and not first.upper().startswith("DISKPART"):
            model = first
    is_usb = bool(
        re.search(r"(?:Type|Tipo|Bus Type|Tipo bus)\s*:\s*.*\bUSB\b", detail, re.I)
        or re.search(r"\bUSB\b", model, re.I)
        or re.search(r"\b(removable|rimovibil)", detail, re.I)
    )
    return model, is_usb


def _system_disk_number() -> int | None:
    """Disco di boot: contrassegnato con * in 'diskpart list disk'."""
    try:
        out = _diskpart_list_disk_output()
    except PrepareError:
        return None
    for line in out.splitlines():
        if "*" not in line:
            continue
        m = re.search(r"(?:Disk|Disco)\s+(\d+)", line, re.I)
        if m:
            return int(m.group(1))
    return None


def physical_drive_path(disk_number: int) -> str:
    return rf"\\.\PhysicalDrive{disk_number}"


def partition_path(disk_number: int, partition_number: int) -> str:
    return rf"\\.\Harddisk{disk_number}\Partition{partition_number}"


def _parse_partition_path(path: str) -> tuple[int, int]:
    m = re.search(r"Harddisk(\d+)\\Partition(\d+)", path, re.I)
    if not m:
        raise PrepareError(f"Percorso partizione non valido: {path}")
    return int(m.group(1)), int(m.group(2))


def _cygwin_dev_path(disk_number: int, partition_number: int) -> str:
    """Cygwin mke2fs: PhysicalDrive0 -> /dev/sda, Partition3 -> /dev/sdb3."""
    drive = chr(ord("a") + disk_number)
    return f"/dev/sd{drive}{partition_number}"


def _partition_offset_bytes(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
) -> int:
    """Byte offset della partizione su PhysicalDrive (diskpart detail)."""
    detail = _run_diskpart(
        [
            f"select disk {disk_number}",
            f"select partition {partition_number}",
            "detail partition",
        ]
    )
    m = _PARTITION_OFFSET_RE.search(detail)
    if not m:
        raise PrepareError(
            f"Offset partizione {partition_number} non trovato in diskpart.\n{detail}"
        )
    offset = int(m.group(1))
    if log:
        log(f"Partizione {partition_number} @ offset {offset} byte ({human_size(offset)})")
    return offset


def _partition_size_bytes(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
) -> int:
    ps = (
        f"$p = Get-Partition -DiskNumber {disk_number} -PartitionNumber "
        f"{partition_number} -ErrorAction Stop; Write-Output $p.Size"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out.isdigit():
        size = int(out)
        if log:
            log(
                f"Partizione {partition_number} dimensione {human_size(size)} "
                f"({size} byte)"
            )
        return size

    sizes = _partition_sizes_bytes_diskpart(disk_number)
    size = sizes.get(partition_number, 0)
    if size <= 0:
        size = _gpt_partition_size_bytes(disk_number, partition_number, log) or 0
    if size <= 0:
        raise PrepareError(
            f"Dimensione partizione {partition_number} non trovata "
            "(Get-Partition / diskpart list / GPT)."
        )
    if log:
        log(
            f"Partizione {partition_number} dimensione {human_size(size)} "
            f"(diskpart list)"
        )
    return size


def _size_unit_to_bytes(value: int, unit: str) -> int:
    mult = {
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    return value * mult[unit.upper()]


def _partition_sizes_bytes_diskpart(disk_number: int) -> dict[int, int]:
    """Dimensioni partizioni da `list partition` (fallback se PowerShell non risponde)."""
    out = _diskpart_list_partitions(disk_number)
    sizes: dict[int, int] = {}
    for m in _PARTITION_LIST_SIZE_RE.finditer(out):
        sizes[int(m.group(1))] = _size_unit_to_bytes(int(m.group(2)), m.group(3))
    return sizes


def _disk_partitions_end_bytes(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> int:
    """Fine (byte) dell'ultima partizione esistente sul disco."""
    ps = (
        f"$dn = {disk_number}; "
        "$parts = Get-Partition -DiskNumber $dn -ErrorAction SilentlyContinue; "
        "if (-not $parts) { Write-Output 0; exit 0 }; "
        "$max = ($parts | ForEach-Object { "
        "[uint64]$_.StartingOffset + [uint64]$_.Size } | Measure-Object -Maximum).Maximum; "
        "Write-Output $max"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out.isdigit():
        end = int(out)
        if log:
            log(f"Fine area partizioni ISO: {human_size(end)}")
        return end

    sizes = _partition_sizes_bytes_diskpart(disk_number)
    end = 0
    for pnum in _list_partition_numbers(disk_number):
        offset = _partition_offset_bytes(disk_number, pnum)
        size = sizes.get(pnum, 0)
        if size <= 0:
            raise PrepareError(
                f"Dimensione partizione {pnum} non trovata in diskpart list partition."
            )
        end = max(end, offset + size)
    if log:
        log(f"Fine area partizioni ISO (diskpart): {human_size(end)}")
    return end


def _prepare_partition_for_mkfs(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Smonta volumi sul disco prima di scrittura raw su partizione."""
    _release_usb_volumes(disk_number, log)
    _dismiss_disk_volumes_ps(disk_number, log)
    _remove_partition_letter(disk_number, partition_number, log)
    time.sleep(1)


def _dismiss_disk_volumes_ps(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Smonta lettere con mountvol /D (senza chiudere Esplora file)."""
    ps = (
        f"$dn = {disk_number}; "
        "Get-Partition -DiskNumber $dn -ErrorAction SilentlyContinue | "
        "ForEach-Object { "
        "if ($_.DriveLetter) { mountvol ($_.DriveLetter + ':') /D 2>$null | Out-Null } "
        "}"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if log and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            log(f"ATTENZIONE smontaggio volumi: {err}")


def root_disk() -> str | None:
    num = _system_disk_number()
    return physical_drive_path(num) if num is not None else None


def list_disks() -> list[DiskInfo]:
    """Elenco dischi fisici via diskpart (Win 7+), senza wmic."""
    try:
        out = _diskpart_list_disk_output()
    except PrepareError as exc:
        raise PrepareError(f"Impossibile elencare i dischi (diskpart):\n{exc}") from exc

    disks: list[DiskInfo] = []
    for line in out.splitlines():
        parsed = _parse_disk_list_line(line)
        if not parsed:
            continue
        idx, size = parsed
        try:
            detail = _disk_detail(idx)
        except PrepareError:
            detail = ""
        model, is_usb = _disk_model_and_usb(detail) if detail else ("—", False)
        disks.append(
            DiskInfo(
                path=physical_drive_path(idx),
                name=str(idx),
                size=size,
                model=model,
                transport="USB" if is_usb else "—",
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


def _parse_partition_list(out: str) -> list[int]:
    return sorted(int(m.group(1)) for m in _PARTITION_LINE_RE.finditer(out))


def _diskpart_list_partitions(disk_number: int) -> str:
    return _run_diskpart([f"select disk {disk_number}", "list partition"])


def _list_partition_numbers(disk_number: int) -> list[int]:
    return _parse_partition_list(_diskpart_list_partitions(disk_number))


def _partition_count(disk_number: int) -> int:
    return len(_list_partition_numbers(disk_number))


def _dismount_letter(letter: str) -> None:
    subprocess.run(
        ["mountvol", f"{letter}:", "/D"],
        capture_output=True,
        text=True,
    )


def _partition_letters(disk_number: int) -> list[tuple[int, str]]:
    out = _diskpart_list_partitions(disk_number)
    found: list[tuple[int, str]] = []
    for pnum in _parse_partition_list(out):
        try:
            detail = _run_diskpart(
                [
                    f"select disk {disk_number}",
                    f"select partition {pnum}",
                    "detail partition",
                ]
            )
        except PrepareError:
            continue
        letter_m = _DRIVE_LETTER_RE.search(detail)
        if letter_m:
            found.append((pnum, letter_m.group(1).upper()))
    return found


def _delete_all_partitions(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    for attempt in range(1, 11):
        parts = _list_partition_numbers(disk_number)
        if not parts:
            return
        if log:
            log(f"Elimino partizioni {parts} (tentativo {attempt}/10)...")
        for pnum in sorted(parts, reverse=True):
            _diskpart_try(
                [
                    f"select disk {disk_number}",
                    f"select partition {pnum}",
                    "delete partition override",
                ],
                log,
                f"delete partition {pnum}",
            )
        time.sleep(1)
    if _list_partition_numbers(disk_number):
        raise PrepareError("Impossibile eliminare tutte le partizioni con diskpart.")


def _clear_disk_powershell(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    if log:
        log(f"Fallback PowerShell Clear-Disk su disco {disk_number}...")
    ps = (
        "$ErrorActionPreference='Stop';"
        f"Clear-Disk -Number {disk_number} -RemoveData -Confirm:$false"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise PrepareError(f"Clear-Disk fallito:\n{out}")
    if log:
        log("Clear-Disk completato.")


def _remove_drive_letters(disk_number: int, log: Callable[[str], None] | None = None) -> None:
    for pnum, letter in _partition_letters(disk_number):
        if log:
            log(f"Smonto {letter}: (partizione {pnum})...")
        _dismount_letter(letter)
        try:
            _run_diskpart(
                [
                    f"select disk {disk_number}",
                    f"select partition {pnum}",
                    f"remove letter={letter}",
                ]
            )
        except PrepareError as exc:
            if log:
                log(f"ATTENZIONE partizione {pnum} (remove letter): {exc}")


def wipe_usb(disk: str, log: Callable[[str], None] | None = None) -> None:
    disk_number = _disk_number(disk)
    if log:
        log(f"Smonto volumi sul disco {disk_number}...")
    try:
        _remove_drive_letters(disk_number, log)
    except PrepareError as exc:
        if log:
            log(f"ATTENZIONE rimozione lettere: {exc}")

    _diskpart_try(
        [f"select disk {disk_number}", "attributes disk clear readonly"],
        log,
        "clear readonly",
    )
    _diskpart_try([f"select disk {disk_number}", "online disk"], log, "online disk")

    if log:
        log("Cancello tabella partizioni (diskpart clean)...")
    cleaned = _diskpart_try([f"select disk {disk_number}", "clean"], log, "clean")

    if _list_partition_numbers(disk_number):
        if log:
            log("clean insufficiente: elimino partizioni una per una...")
        try:
            _delete_all_partitions(disk_number, log)
        except PrepareError as exc:
            if log:
                log(str(exc))

    if _list_partition_numbers(disk_number):
        try:
            _clear_disk_powershell(disk_number, log)
        except PrepareError as exc:
            if log:
                log(str(exc))
            if not cleaned:
                raise PrepareError(
                    "Impossibile azzerare la chiavetta USB.\n"
                    f"Dettaglio: {exc}"
                ) from exc

    if _list_partition_numbers(disk_number):
        raise PrepareError(
            "La chiavetta ha ancora partizioni dopo la pulizia.\n"
            "Riprova: la GUI chiude Esplora file automaticamente durante l'operazione."
        )

    _strip_all_letters_on_disk(disk_number, log)
    time.sleep(1)
    _strip_all_letters_on_disk(disk_number, log)
    if log:
        log("Chiavetta azzerata: pronta per scrittura ISO.")


def settle_usb_before_partition(disk: str, log: Callable[[str], None] | None = None) -> bool:
    disk_number = _disk_number(disk)
    for attempt in range(5):
        _remove_drive_letters(disk_number, log)
        time.sleep(1)
        out = _diskpart_list_partitions(disk_number)
        still_assigned = False
        for pnum in _parse_partition_list(out):
            try:
                detail = _run_diskpart(
                    [
                        f"select disk {disk_number}",
                        f"select partition {pnum}",
                        "detail partition",
                    ]
                )
            except PrepareError:
                continue
            if _DRIVE_LETTER_RE.search(detail):
                still_assigned = True
                break
        if not still_assigned:
            return True
        if log:
            log(f"Disco ancora con lettere assegnate (tentativo {attempt + 1}/5)...")
        time.sleep(1)
    return False


def _automount_disable(log: Callable[[str], None] | None = None) -> None:
    subprocess.run(["mountvol", "/N"], capture_output=True, creationflags=_ps_run_flags())
    if log:
        log("Automount disattivato (evita finestre «Formatta unità»).")


def _automount_enable(log: Callable[[str], None] | None = None) -> None:
    subprocess.run(["mountvol", "/E"], capture_output=True, creationflags=_ps_run_flags())
    if log:
        log("Automount riattivato.")


def _shell_hw_detection_stop(log: Callable[[str], None] | None = None) -> None:
    """
    Ferma ShellHWDetection: evita il popup «È necessario formattare il disco»
    quando dopo clean Windows vede un volume RAW con lettera.
    (Stesso trucco usato da Etcher / script di imaging USB.)
    """
    proc = subprocess.run(
        ["net", "stop", "ShellHWDetection"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_ps_run_flags(),
    )
    if log:
        if proc.returncode == 0:
            log("Servizio ShellHWDetection sospeso (niente popup «Formatta disco»).")
        else:
            msg = ((proc.stderr or proc.stdout) or "").strip()
            _emit_log(
                log,
                "ATTENZIONE: impossibile sospendere ShellHWDetection"
                + (f" — {msg}" if msg else "")
                + ". Il popup «Formatta» potrebbe ancora apparire.",
            )


def _shell_hw_detection_start(log: Callable[[str], None] | None = None) -> None:
    proc = subprocess.run(
        ["net", "start", "ShellHWDetection"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_ps_run_flags(),
    )
    if log:
        if proc.returncode == 0:
            log("Servizio ShellHWDetection ripristinato.")
        else:
            # Già avviato = ok
            out = ((proc.stdout or "") + (proc.stderr or "")).lower()
            if "already" in out or "già" in out or "gia" in out:
                log("Servizio ShellHWDetection già attivo.")
            else:
                _emit_log(
                    log,
                    "ATTENZIONE: ripristino ShellHWDetection non confermato — "
                    "in caso di problemi: services.msc → Shell Hardware Detection → Avvia.",
                )


def _strip_all_letters_on_disk(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Toglie lettere subito dopo clean/write per non far spuntare dialoghi RAW."""
    _remove_drive_letters(disk_number, log)
    ps = (
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"Get-Partition -DiskNumber {disk_number} | ForEach-Object {{ "
        "  if ($_.DriveLetter) { "
        "    $L = [string]$_.DriveLetter; "
        "    Remove-PartitionAccessPath -DiskNumber $_.DiskNumber "
        "      -PartitionNumber $_.PartitionNumber -AccessPath ($L + ':\\') "
        "      -ErrorAction SilentlyContinue; "
        "    mountvol ($L + ':') /D 2>$null | Out-Null "
        "  } "
        "}; "
        "Get-Volume | Where-Object { "
        "  $_.DriveType -eq 'Removable' -and $_.DriveLetter "
        "} | ForEach-Object { mountvol ($_.DriveLetter + ':') /D 2>$null | Out-Null }"
    )
    _powershell(ps, log=log, label="strip lettere USB")


def _release_usb_volumes(disk_number: int, log: Callable[[str], None] | None = None) -> None:
    """Smonta volumi USB senza diskpart rescan (evita remount e popup)."""
    for pnum, letter in _partition_letters(disk_number):
        if log:
            log(f"Smonto volume {letter}: (partizione {pnum})...")
        _dismount_letter(letter)
        _diskpart_try(
            [
                f"select disk {disk_number}",
                f"select partition {pnum}",
                f"remove letter={letter}",
            ],
            log,
            f"remove letter {letter}",
        )
    time.sleep(1)


def _rescan_disk(disk_number: int, log: Callable[[str], None] | None = None) -> None:
    try:
        _run_diskpart([f"select disk {disk_number}", "rescan"])
    except PrepareError as exc:
        if log:
            log(f"ATTENZIONE rescan: {exc}")
    time.sleep(2)


def _prepare_raw_disk_io(
    disk_number: int,
    log: Callable[[str], None] | None = None,
    *,
    rescan: bool = True,
) -> None:
    """Smonta volumi; rescan opzionale (evita remount prima di verify ISO)."""
    _release_usb_volumes(disk_number, log)
    if rescan:
        _rescan_disk(disk_number, log)


def _suppress_automount_volumes(
    disk_number: int,
    log: Callable[[str], None] | None = None,
    *,
    hide_boot: bool = True,
    keep_home_letter: bool = False,
    home_partition: int | None = None,
) -> None:
    """
    Nasconde solo boot EFI (pochi MiB). Non tocca mai QUELO-HOME.
    Nota: numeri GPT (#4) ≠ numeri Windows (home spesso #3).
    """
    del keep_home_letter, home_partition  # legacy API: non usati (evita remove errati)
    home_letter = _volume_drive_letter(HOME_LABEL)
    for pnum in _list_partition_numbers(disk_number):
        letter = _partition_drive_letter(disk_number, pnum)
        if letter and home_letter and letter.upper() == home_letter.upper():
            continue
        if letter and _volume_label_for_letter(letter) == HOME_LABEL:
            continue
        try:
            size = _partition_size_bytes(disk_number, pnum, log=None)
        except PrepareError:
            size = 0
        # Solo EFI / sistema isohybrid (tipicamente ~3 MiB)
        if hide_boot and 0 < size <= 16 * 1024 * 1024:
            _remove_partition_letter(disk_number, pnum, log)
            _set_partition_hidden(disk_number, pnum, True, log)


def reread_partition_table(disk: str) -> None:
    disk_number = _disk_number(disk)
    _rescan_disk(disk_number)


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


_PROGRESS_BAR_WIDTH = 24


def _fmt_progress_bar(pct: int) -> str:
    pct = max(0, min(100, pct))
    filled = pct * _PROGRESS_BAR_WIDTH // 100
    return "#" * filled + "-" * (_PROGRESS_BAR_WIDTH - filled)


class _ProgressReporter:
    """Sotto-progresso con barra testuale (LOG-FULL) e barra GUI."""

    def __init__(
        self,
        log: Callable[[str], None] | None,
        progress: ProgressCallback | None,
        *,
        gui_start: int,
        gui_end: int,
        phase_title: str,
        phase_step: str,
    ) -> None:
        self._log = log
        self._progress = progress
        self._gui_start = gui_start
        self._gui_end = gui_end
        self._phase_title = phase_title
        self._phase_step = phase_step
        self._t0 = time.time()
        self._last_log = 0.0
        self._last_sub_pct = -1

    def _gui_pct(self, sub_pct: int) -> int:
        sub_pct = max(0, min(100, sub_pct))
        span = self._gui_end - self._gui_start
        return self._gui_start + sub_pct * span // 100

    def _format_line(
        self,
        sub_pct: int,
        detail: str = "",
        *,
        current: int = 0,
        total: int = 0,
    ) -> str:
        elapsed = int(time.time() - self._t0)
        bar = _fmt_progress_bar(sub_pct)
        parts = [f"[{bar}] {sub_pct:3d}%", self._phase_step]
        if detail:
            parts.append(detail)
        elif total > 0:
            parts.append(f"{human_size(current)}/{human_size(total)}")
        parts.append(f"{elapsed}s")
        return "  ".join(parts)

    def report(
        self,
        sub_pct: int,
        detail: str = "",
        *,
        current: int = 0,
        total: int = 0,
        force: bool = False,
    ) -> None:
        sub_pct = max(0, min(100, sub_pct))
        line = self._format_line(sub_pct, detail, current=current, total=total)
        gui_msg = f"{self._phase_title}\n{line}"
        if self._progress:
            self._progress(self._gui_pct(sub_pct), current, total, gui_msg)

        now = time.time()
        should_log = (
            force
            or sub_pct in (0, 100)
            or self._last_sub_pct < 0
            or sub_pct - self._last_sub_pct >= 5
            or now - self._last_log >= 1.0
        )
        if should_log:
            _emit_log(self._log, line)
            self._last_log = now
        self._last_sub_pct = sub_pct


def _wrap_disk_write_progress(
    disk_obj,
    reporter: _ProgressReporter,
    est_bytes: int,
) -> None:
    """Intercetta write() FATtools per mostrare avanzamento durante mkfs."""
    written = 0
    orig_write = disk_obj.write

    def write(s) -> None:
        nonlocal written
        orig_write(s)
        written += len(s)
        est = max(est_bytes, 16 * 1024 * 1024)
        sub = min(95, 5 + written * 90 // est)
        reporter.report(sub, f"{human_size(written)} scritti")

    disk_obj.write = write


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
    disk_number = _disk_number(disk)
    _dismiss_disk_volumes_ps(disk_number, log)
    _prepare_raw_disk_io(disk_number, log, rescan=False)
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

    rc = mod.write_iso(
        iso_path,
        disk,
        use_direct=False,
        progress_callback=writer_progress,
        verify_after=True,
    )
    if rc != 0:
        raise PrepareError("Scrittura o verifica ISO fallita.")
    disk_number = _disk_number(disk)
    _suppress_automount_volumes(disk_number, log, hide_boot=True)
    _rescan_disk(disk_number, log)


def verify_new_partitions(disk_number: int, persist_num: int, home_num: int) -> tuple[str, str]:
    persist = partition_path(disk_number, persist_num)
    home = partition_path(disk_number, home_num)
    return persist, home


def create_partitions_auto(
    disk: str,
    persist_mb: int,
    iso_size_bytes: int,
    log: Callable[[str], None] | None = None,
    step: Callable[[int, str], None] | None = None,
) -> tuple[str, str, int, int, int, int]:
    """
    Dopo dd ISO ibrida: append GPT + MBR (equivalente sfdisk --append su Linux).

    Isohybrid: MBR non-protettivo → Linux/Windows ignorano la GPT e usano solo MBR.
    Serve scrivere slot MBR 3 (ext4) e 4 (exFAT), come fa fdisk/sfdisk su Linux.
    La GPT resta aggiornata per tool UEFI; sgdisk rifiuta comunque le ISO ibride.
    """
    disk_number = _disk_number(disk)
    disk_path = physical_drive_path(disk_number)
    for attempt in range(1, 4):
        if step:
            step(
                82 + attempt,
                "Fase 4 — Creazione partizioni per persistenza e dati utente\n"
                f"Tentativo {attempt} di 3: append GPT dopo l'ISO (stile sfdisk) — "
                f"ext4 da {persist_mb} MB («{PERSIST_LABEL}») "
                f"e exFAT («{HOME_LABEL}») con lo spazio rimanente.",
            )
        _emit_log(log, f"Append GPT sfdisk-style (tentativo {attempt}/3)...")
        settle_usb_before_partition(disk, log)
        try:
            before = _list_partition_numbers(disk_number)
            _emit_log(
                log,
                f"Partizioni Windows (diskpart): {before or '(solo ISO visibile — normale)'}",
            )

            min_offset_bytes = _probe_append_offset_after_iso(
                disk_path, disk_number, iso_size_bytes, persist_mb, log
            )
            persist_off, home_off = _gpt_append_two_partitions(
                disk_path, disk_number, min_offset_bytes, persist_mb, log
            )

            if persist_off < iso_size_bytes or home_off < iso_size_bytes:
                raise PrepareError(
                    f"BLOCCO sicurezza: offset persist/home < ISO "
                    f"{human_size(iso_size_bytes)}."
                )
            disk_bytes = _disk_size_bytes(disk_number)
            if persist_off >= disk_bytes or home_off >= disk_bytes:
                raise PrepareError(
                    f"BLOCCO sicurezza: offset persist @ {human_size(persist_off)} "
                    f"o home @ {human_size(home_off)} oltre disco "
                    f"{human_size(disk_bytes)}."
                )

            _rescan_disk_aggressive(disk_number, log)
            layout = _gpt_load(disk_path, log=None)
            persist_num, home_num, verify_p_off, verify_h_off = _gpt_quelo_partition_numbers(
                layout, log
            )
            if verify_p_off != persist_off or verify_h_off != home_off:
                raise PrepareError(
                    f"Offset GPT incoerenti: append persist {persist_off} vs "
                    f"lettura {verify_p_off}, home {home_off} vs {verify_h_off}."
                )

            _emit_log(
                log,
                f"Partizioni GPT: persist=#{persist_num} @ {human_size(persist_off)}, "
                f"home=#{home_num} @ {human_size(home_off)}",
            )
            persist_path, home_path = verify_new_partitions(
                disk_number, persist_num, home_num
            )

            def _gpt_span_size(part_num: int) -> int:
                for number, (first_lba, last_lba, _name) in enumerate(
                    _gpt_list_partitions(layout), start=1
                ):
                    if number == part_num:
                        return (last_lba - first_lba + 1) * SECTOR_SIZE
                raise PrepareError(
                    f"Dimensione GPT partizione {part_num} non trovata dopo append."
                )

            persist_size = _gpt_span_size(persist_num)
            home_size = _gpt_span_size(home_num)
            _emit_log(
                log,
                f"Dimensioni GPT: persist {human_size(persist_size)}, "
                f"home {human_size(home_size)}",
            )
            return (
                persist_path,
                home_path,
                persist_off,
                home_off,
                persist_size,
                home_size,
            )
        except PrepareError as exc:
            _emit_log(log, str(exc))
            time.sleep(2)
    raise PrepareError("Creazione automatica partizioni fallita dopo 3 tentativi.")


def _format_com_exe() -> str:
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(windir, "System32", "format.com")


def _hide_partition(disk_number: int, partition_number: int, log: Callable[[str], None] | None = None) -> None:
    _remove_partition_letter(disk_number, partition_number, log)
    _set_partition_hidden(disk_number, partition_number, True, log)


def _create_home_tree(root: str, log: Callable[[str], None] | None = None) -> None:
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
    common.write_windows_boot_protect_files(root)
    with open(os.path.join(home_dir, ".quelo-prepared"), "w", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
    if log:
        log("Cartelle home, quelo-export e NASCONDI-BOOT-WINDOWS.bat create.")


def _ensure_fattools_path() -> None:
    lib = os.path.join(script_dir(), "windows", "python", "Lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)


def _fattools_write_text(root, name: str, content: str, *, encoding: str = "utf-8") -> None:
    data = content.encode(encoding)
    cluster = root.boot.cluster
    prealloc = max(1, (len(data) + cluster - 1) // cluster)
    fh = root.create(name, prealloc)
    fh.write(bytearray(data))
    fh.close()


def _create_home_tree_fattools(root, log: Callable[[str], None] | None = None) -> None:
    home = root.mkdir("home")
    if not home:
        raise PrepareError("FATtools: impossibile creare cartella home.")
    for name in (
        "Desktop",
        "Documenti",
        "Scaricati",
        "Immagini",
        "Musica",
        "Video",
        "Modelli",
    ):
        if not home.mkdir(name):
            raise PrepareError(f"FATtools: impossibile creare home/{name}.")
    if not root.mkdir("quelo-export"):
        raise PrepareError("FATtools: impossibile creare quelo-export.")
    _fattools_write_text(root, "NASCONDI-BOOT-WINDOWS.bat", common.WIN_BOOT_PROTECT_BAT, encoding="ascii")
    _fattools_write_text(root, "LEGGIMI-BOOT-WINDOWS.txt", common.WIN_BOOT_PROTECT_TXT)
    prepared = home.create(".quelo-prepared", 1)
    prepared.write(bytearray(time.strftime("%Y-%m-%dT%H:%M:%S").encode("utf-8")))
    prepared.close()
    if log:
        log("Cartelle home, quelo-export e NASCONDI-BOOT-WINDOWS.bat create (FATtools).")


def _format_exfat_fattools_in_place(
    target_disk_number: int,
    home_offset_bytes: int,
    home_size_bytes: int,
    label: str,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    """
    Formatta exFAT direttamente su PhysicalDrive @ offset GPT (FATtools).
    Evita VHD scratch e \\.\\HarddiskN\\PartitionM (non apribili in lettura).
    """
    _ensure_fattools_path()
    from FATtools.disk import disk, partition
    from FATtools.mkfat import exfat_mkfs
    from FATtools.Volume import openvolume

    reporter = _ProgressReporter(
        log,
        progress,
        gui_start=93,
        gui_end=97,
        phase_title="Fase 5 — Formattazione partizione home (exFAT)",
        phase_step="exFAT (FATtools)",
    )

    disk_path = physical_drive_path(target_disk_number)
    if log:
        log(
            f"Windows non enumera la partizione home — FATtools exFAT in-place "
            f"@ {human_size(home_offset_bytes)} ({human_size(home_size_bytes)}) "
            f"su {disk_path}..."
        )
    reporter.report(0, "Preparazione volume...", force=True)

    d = disk(disk_path, "r+b")
    try:
        _wrap_disk_write_progress(
            d, reporter, max(home_size_bytes // 8, 64 * 1024 * 1024)
        )
        part = partition(d, home_offset_bytes, home_size_bytes)
        try:
            reporter.report(5, "Scrittura strutture exFAT (FAT, bitmap)...", force=True)
            rc = exfat_mkfs(part, part.size, params={"show_info": 0})
            if rc != 0:
                raise PrepareError(f"FATtools exfat_mkfs fallito (codice {rc}).")
            reporter.report(96, "Strutture exFAT scritte", force=True)

            root = openvolume(part)
            if isinstance(root, str):
                raise PrepareError(
                    f"FATtools: filesystem non riconosciuto dopo mkfs ({root})."
                )

            reporter.report(97, f"Etichetta «{label}» e cartelle utente...", force=True)
            root.label(label)
            _create_home_tree_fattools(root, log)
            root.flush()
            part.flush()
            reporter.report(100, "exFAT completato", force=True)
        finally:
            part.close()
    finally:
        d.close()

    if log:
        log(
            f"exFAT creato ({label}) @ {human_size(home_offset_bytes)} via FATtools"
        )


def _format_exfat_on_letter(
    letter: str,
    label: str,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    reporter = _ProgressReporter(
        log,
        progress,
        gui_start=93,
        gui_end=97,
        phase_title="Fase 5 — Formattazione partizione home (exFAT)",
        phase_step=f"exFAT su {letter}:",
    )
    reporter.report(0, "Avvio Format-Volume...", force=True)
    ps = (
        f"$ErrorActionPreference='Stop';"
        f"$letter = '{letter}'; $label = '{label}'; "
        "Format-Volume -DriveLetter $letter -FileSystem exFAT "
        "-NewFileSystemLabel $label -Confirm:$false -Force -ErrorAction Stop | Out-Null"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        creationflags=_ps_run_flags(),
    )
    if proc.returncode == 0:
        reporter.report(100, f"exFAT creato ({label})", force=True)
        if log:
            _emit_log(log, f"exFAT creato ({label}) via Format-Volume su {letter}:")
        return

    err = (proc.stderr or proc.stdout or "").strip()
    if log:
        _emit_log(log, f"Format-Volume fallito, provo format.com: {err}")
    reporter.report(50, "Format-Volume fallito, provo format.com...", force=True)
    format_exe = _format_com_exe()
    if not os.path.isfile(format_exe):
        raise PrepareError(f"format.com non trovato: {format_exe}")
    if log:
        log(f"Formatto {letter}: exFAT ({label}) con format.com...")
    proc = subprocess.run(
        [format_exe, f"{letter}:", "/FS:exFAT", f"/V:{label}", "/Q", "/Y"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        creationflags=_ps_run_flags(),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise PrepareError(f"format exFAT fallito: {err}")
    reporter.report(100, f"exFAT creato ({label})", force=True)
    if log:
        log(f"exFAT creato ({label}) su {letter}:")


def _try_ensure_home_letter(
    disk_number: int,
    partition_number: int,
    home_offset_bytes: int | None = None,
    log: Callable[[str], None] | None = None,
    *,
    home_size_bytes: int | None = None,
    max_attempts: int = 5,
    allow_rescan: bool = True,
    prefer_volume_label: bool = False,
) -> str | None:
    if prefer_volume_label:
        try:
            return _mount_home_exfat_volume(
                disk_number,
                log,
                home_size_bytes=home_size_bytes,
                home_partition_number=partition_number,
            )
        except PrepareError as exc:
            if log:
                log(str(exc))
            return None

    letter = _volume_drive_letter(HOME_LABEL)
    if letter:
        return letter

    letter = _partition_drive_letter(disk_number, partition_number)
    if letter:
        return letter
    part_n = partition_number
    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and allow_rescan:
            _rescan_disk_aggressive(disk_number, log)
        if allow_rescan and home_offset_bytes is not None:
            try:
                part_n = _partition_number_at_offset(
                    disk_number, home_offset_bytes, log, size_bytes=None
                )
                letter = _partition_drive_letter(disk_number, part_n)
                if letter:
                    return letter
            except PrepareError:
                pass
        letter = _free_drive_letter()
        if _diskpart_try(
            [
                f"select disk {disk_number}",
                f"select partition {part_n}",
                f"assign letter={letter}",
            ],
            log,
            f"assign home {letter}",
        ):
            return letter
    return None


def _format_exfat(
    partition_path: str,
    label: str,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
    *,
    home_offset_bytes: int | None = None,
    home_size_bytes: int | None = None,
    min_write_offset_bytes: int = 0,
) -> bool:
    """
    Formatta exFAT. Ritorna True se cartelle home già create via FATtools in-place.
    """
    disk_n, part_n = _parse_partition_path(partition_path)
    _prepare_partition_for_mkfs(disk_n, part_n, log)

    use_scratch = part_n not in _list_partition_numbers(disk_n)
    if not use_scratch:
        letter = _free_drive_letter()
        try:
            _assign_partition_letter(disk_n, part_n, letter, log)
        except PrepareError as exc:
            if log:
                log(f"ATTENZIONE assign lettera exFAT: {exc}")
            use_scratch = True
        else:
            _format_exfat_on_letter(letter, label, log, progress)
            return False

    if home_offset_bytes is None or not home_size_bytes:
        raise PrepareError(
            f"Partizione {part_n} non enumerata da Windows e offset GPT home assente."
        )
    if min_write_offset_bytes > 0 and home_offset_bytes < min_write_offset_bytes:
        raise PrepareError(
            f"BLOCCO sicurezza: home @ {human_size(home_offset_bytes)} "
            f"< {human_size(min_write_offset_bytes)}."
        )
    _format_exfat_fattools_in_place(
        disk_n, home_offset_bytes, home_size_bytes, label, log, progress
    )
    try:
        _gpt_resync_quelo_table(physical_drive_path(disk_n), disk_n, log)
        _rescan_disk_aggressive(disk_n, log)
    except PrepareError as exc:
        if log:
            _emit_log(log, f"ATTENZIONE resync GPT post-exFAT: {exc}")
    # Non forzare lettera T/U qui: Windows assegna da sola dopo automount.
    if log:
        log(
            f"exFAT «{label}» pronto — lettera di unità lasciata a Windows (automount)."
        )
    return True


def _windows_assign_home_any_letter(
    disk_number: int,
    log: Callable[[str], None] | None = None,
) -> str | None:
    """Chiede a Windows la prossima lettera libera (senza forzare T/U)."""
    found = _diskpart_volume_by_label(HOME_LABEL)
    if found is None:
        return None
    vol_num, existing = found
    if existing and os.path.isdir(f"{existing}:\\"):
        return existing.upper()
    # diskpart «assign» senza letter= → Windows sceglie la prima libera
    if _diskpart_try(
        [f"select volume={vol_num}", "assign"],
        log,
        f"assign automatico volume {vol_num} ({HOME_LABEL})",
    ):
        letter = _volume_drive_letter(HOME_LABEL)
        if letter:
            return letter
    return _volume_drive_letter(HOME_LABEL)


def _finalize_home_volume(
    disk_number: int,
    partition_number: int,
    log: Callable[[str], None] | None = None,
    *,
    home_offset_bytes: int | None = None,
    home_size_bytes: int | None = None,
    fattools_in_place: bool = False,
) -> str:
    del partition_number, home_offset_bytes, home_size_bytes  # numeri GPT ≠ Windows
    # Critico: riabilita automount PRIMA di qualsiasi check lettera
    _automount_enable(log)
    subprocess.run(
        ["mountvol", "/E"],
        capture_output=True,
        creationflags=_ps_run_flags(),
    )

    letter = _volume_drive_letter(HOME_LABEL)
    if letter and os.path.isdir(f"{letter}:\\"):
        if log:
            log(f"Windows ha già montato «{HOME_LABEL}» su {letter}:")
        return letter

    if log:
        log(
            f"Attendo che Windows assegni automaticamente una lettera a «{HOME_LABEL}»..."
        )
    for attempt in range(1, 9):
        _diskpart_try(
            [f"select disk {disk_number}", "rescan"],
            None,
            "rescan home",
        )
        time.sleep(1 if attempt <= 3 else 2)
        letter = _volume_drive_letter(HOME_LABEL)
        if letter and os.path.isdir(f"{letter}:\\"):
            if log:
                if os.path.isdir(os.path.join(f"{letter}:\\", "home")):
                    log(f"Verifica OK: cartella {letter}:\\home (lettera Windows).")
                log(
                    f"Home exFAT «{HOME_LABEL}» → {letter}: "
                    "(assegnata da Windows, non forzata T/U)."
                )
            return letter

    # Un solo tentativo gentile: assign senza lettera preferita
    letter = _windows_assign_home_any_letter(disk_number, log)
    if letter and os.path.isdir(f"{letter}:\\"):
        if log:
            log(
                f"Home exFAT «{HOME_LABEL}» → {letter}: "
                "(assign Windows senza preferenza T/U)."
            )
        return letter

    if log:
        log(
            f"«{HOME_LABEL}» è pronta sulla chiavetta. "
            "Se non compare in Esplora file: espelli e reinserisci la USB — "
            "Windows assegnerà la lettera da sola."
        )
    # Non fallire: partizione + FS ok (verificato su Linux); solo la lettera manca.
    return ""


def _run_mke2fs_file_image(
    persist_part: str,
    label: str,
    size_mb: int,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
    *,
    min_write_offset_bytes: int = 0,
    persist_offset_bytes: int | None = None,
    persist_size_bytes: int | None = None,
) -> None:
    """
    ext4 via file immagine + copia raw sulla partizione.
    L'immagine copre l'intera partizione (byte reali da Get-Partition), come
    mkfs.ext4 su Linux — evita mismatch MB vs immagine che corrompe la home.
    """
    mke2fs = _mke2fs_exe()
    tools = _tools_dir()
    disk_n, part_n = _parse_partition_path(persist_part)
    _prepare_partition_for_mkfs(disk_n, part_n, log)

    img_name = "quelo-persist-tmp.img"
    img_path = os.path.join(tools, img_name)
    nominal_bytes = size_mb * 1024 * 1024
    if persist_size_bytes is not None and persist_size_bytes > 0:
        partition_bytes = persist_size_bytes
        if log:
            log(
                f"Dimensione persist da GPT append: {human_size(partition_bytes)} "
                f"({partition_bytes} byte)"
            )
    else:
        partition_bytes = _partition_size_bytes(disk_n, part_n, log)
    if partition_bytes <= 0:
        raise PrepareError(
            f"Dimensione partizione {part_n} non valida ({partition_bytes} byte)."
        )
    if nominal_bytes != partition_bytes and log:
        log(
            f"ATTENZIONE: partizione persist = {human_size(partition_bytes)} "
            f"(GUI {size_mb} MB = {human_size(nominal_bytes)}). "
            "Formatto l'intera partizione reale."
        )
    size_bytes = partition_bytes

    reporter = _ProgressReporter(
        log,
        progress,
        gui_start=90,
        gui_end=93,
        phase_title="Fase 5 — Formattazione partizione persistenza (ext4)",
        phase_step="ext4 (persistence)",
    )

    env = os.environ.copy()
    if os.path.isdir(tools):
        env["PATH"] = tools + os.pathsep + env.get("PATH", "")
    env.setdefault("CYGWIN", "nodosfilewarning")

    run_flags = _ps_run_flags()
    mke2fs_args = [
        mke2fs,
        "-F",
        "-t",
        "ext4",
        "-L",
        label,
        "-E",
        "lazy_itable_init=0,lazy_journal_init=0",
        img_name,
    ]

    if log:
        log(
            f"Creo immagine ext4 temporanea ({human_size(size_bytes)}, "
            f"{size_bytes} byte)..."
        )
    reporter.report(0, f"Preparazione immagine {human_size(size_bytes)}...", force=True)
    try:
        with open(img_path, "wb") as fh:
            fh.truncate(size_bytes)
        reporter.report(5, "Immagine temporanea pronta", force=True)

        out_path = os.path.join(tempfile.gettempdir(), f"quelo-mke2fs-{disk_n}-{part_n}.log")
        if log:
            log(f"mke2fs -F -t ext4 -L {label} -E lazy_itable_init=0,lazy_journal_init=0 {img_name}")
        reporter.report(10, "Avvio mke2fs...", force=True)
        stop_ticker = threading.Event()

        def _mke2fs_ticker() -> None:
            t0 = time.time()
            while not stop_ticker.wait(2.0):
                elapsed = int(time.time() - t0)
                sub = min(38, 10 + elapsed)
                reporter.report(sub, f"mke2fs in corso ({elapsed}s)")

        ticker = threading.Thread(target=_mke2fs_ticker, daemon=True)
        ticker.start()
        try:
            with open(out_path, "wb") as out_fh:
                proc = subprocess.run(
                    mke2fs_args,
                    stdin=subprocess.DEVNULL,
                    stdout=out_fh,
                    stderr=subprocess.STDOUT,
                    cwd=tools if os.path.isdir(tools) else None,
                    env=env,
                    timeout=180,
                    creationflags=run_flags,
                )
            with open(out_path, "rb") as out_fh:
                out = out_fh.read().decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired as exc:
            raise PrepareError("mke2fs scaduto dopo 180s.") from exc
        finally:
            stop_ticker.set()
            ticker.join(timeout=1.0)
            try:
                os.unlink(out_path)
            except OSError:
                pass

        if proc.returncode != 0:
            err = out or f"exit code {proc.returncode}"
            if proc.returncode == 3221225781:
                err = (
                    f"{err}\n"
                    "DLL Cygwin mancante (0xC0000135). "
                    "Reinstalla da zip aggiornato o verifica windows\\tools\\e2fsprogs\\."
                )
            raise PrepareError(f"mke2fs su file immagine fallito.\n{err}")

        reporter.report(40, "mke2fs completato", force=True)
        if log and out:
            log(out)

        formatted = os.path.getsize(img_path)
        if formatted < size_bytes:
            with open(img_path, "ab") as fh:
                fh.truncate(size_bytes)
            if log:
                log(
                    f"Immagine allineata a {human_size(size_bytes)} "
                    f"(mke2fs ha scritto {human_size(formatted)})."
                )
        elif formatted > size_bytes:
            raise PrepareError(
                f"Immagine ext4 ({human_size(formatted)}) supera la partizione "
                f"({human_size(size_bytes)})."
            )

        disk_path = physical_drive_path(disk_n)
        if persist_offset_bytes is not None:
            offset = persist_offset_bytes
        else:
            offset = _partition_offset_bytes(disk_n, part_n, log)
        if min_write_offset_bytes > 0 and offset < min_write_offset_bytes:
            raise PrepareError(
                f"BLOCCO sicurezza: persist @ {human_size(offset)} "
                f"< {human_size(min_write_offset_bytes)} — "
                "non scrivo ext4 per proteggere l'ISO/squashfs."
            )
        actual = os.path.getsize(img_path)
        if log:
            log(
                f"Copio immagine ext4 ({human_size(actual)}) su {disk_path} "
                f"@ offset {offset} (partizione {part_n})..."
            )
        reporter.report(45, "Copia immagine su USB...", force=True)

        def _copy_progress(total: int, current: int, elapsed: int, _label: str) -> None:
            sub = 45 + (current * 55 // total) if total else 45
            reporter.report(sub, current=current, total=total)

        mod = load_write_iso_module()
        rc = mod.write_file_to_device(
            img_path,
            disk_path,
            progress_callback=_copy_progress,
            offset_bytes=offset,
        )
        if rc != 0:
            raise PrepareError(
                f"Copia immagine ext4 su partizione {part_n} di {disk_path} fallita."
            )
        reporter.report(100, f"{human_size(actual)} scritti", force=True)
        if log:
            log(f"ext4 creato ({label}) su {persist_part} — {human_size(actual)} scritti")
    finally:
        try:
            os.unlink(img_path)
        except OSError:
            pass


def format_partitions(
    persist_part: str,
    home_part: str,
    persist_mb: int,
    log: Callable[[str], None] | None = None,
    step: Callable[[int, str], None] | None = None,
    progress: ProgressCallback | None = None,
    *,
    min_write_offset_bytes: int = 0,
    persist_offset_bytes: int | None = None,
    persist_size_bytes: int | None = None,
    home_offset_bytes: int | None = None,
    home_size_bytes: int | None = None,
) -> bool:
    """
    Formatta persist (ext4) e home (exFAT).
    Ritorna True se le cartelle home sono già state create su scratch VHD.
    """
    if step:
        step(
            90,
            "Fase 5 — Formattazione partizione persistenza (ext4)\n"
            f"Preparo la partizione con filesystem ext4 ed etichetta «{PERSIST_LABEL}».",
        )
    if log:
        log(f"Formatto {persist_part} (ext4, {PERSIST_LABEL})...")
    _run_mke2fs_file_image(
        persist_part,
        PERSIST_LABEL,
        persist_mb,
        log,
        progress,
        min_write_offset_bytes=min_write_offset_bytes,
        persist_offset_bytes=persist_offset_bytes,
        persist_size_bytes=persist_size_bytes,
    )
    disk_n, persist_n = _parse_partition_path(persist_part)
    _hide_partition(disk_n, persist_n, log)

    if step:
        step(
            93,
            "Fase 5 — Formattazione partizione home (exFAT)\n"
            f"Preparo la partizione con filesystem exFAT ed etichetta «{HOME_LABEL}».",
        )
    if log:
        log(f"Formatto {home_part} (exFAT, {HOME_LABEL})...")
    return _format_exfat(
        home_part,
        HOME_LABEL,
        log,
        progress,
        home_offset_bytes=home_offset_bytes,
        home_size_bytes=home_size_bytes,
        min_write_offset_bytes=min_write_offset_bytes,
    )


def setup_home_folders(
    home_part: str,
    log: Callable[[str], None] | None = None,
    *,
    skip_tree: bool = False,
    home_offset_bytes: int | None = None,
) -> str:
    disk_n, part_n = _parse_partition_path(home_part)
    if skip_tree:
        if log:
            log("Cartelle home già incluse nell'immagine exFAT (FATtools in-place).")
        letter = _volume_drive_letter(HOME_LABEL)
        if letter and log:
            log(f"Home exFAT già montata su {letter}:\\")
        return letter or ""

    letter = _ensure_partition_letter(disk_n, part_n, log)
    if log:
        log(f"Monto home exFAT su {letter}:\\...")
    _create_home_tree(f"{letter}:\\", log)
    return letter


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

    disk_number = _disk_number(disk)
    _automount_disable(log)
    _shell_hw_detection_stop(log)

    try:
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

        step(
            82,
            "Fase 4 — Creazione partizioni per persistenza e dati utente\n"
            f"Nello spazio libero dopo l'ISO creo persistenza ext4 da {persist_mb} MB "
            "e home exFAT per i file personali.",
        )
        if log:
            log("PASSO 6/9 — Creazione partizioni")
        if not settle_usb_before_partition(disk, log) and log:
            _emit_log(log, "ATTENZIONE: disco ancora con volumi aperti (Esplora risorse?).")
        iso_size = os.path.getsize(iso_path)
        persist_part, home_part, persist_off, home_off, persist_size, home_size = (
            create_partitions_auto(disk, persist_mb, iso_size, log, step=step)
        )

        if log:
            log("PASSO 7/9 — Formattazione")
        home_tree_on_scratch = format_partitions(
            persist_part,
            home_part,
            persist_mb,
            log,
            step=step,
            progress=progress,
            min_write_offset_bytes=iso_size,
            persist_offset_bytes=persist_off,
            persist_size_bytes=persist_size,
            home_offset_bytes=home_off,
            home_size_bytes=home_size,
        )
        _suppress_automount_volumes(disk_number, log, hide_boot=True)

        step(
            95,
            "Fase 6 — Preparazione cartelle e area export\n"
            "Creo le cartelle utente standard e «quelo-export» sulla partizione home exFAT.",
        )
        if log:
            log("PASSO 8/9 — Cartelle home exFAT")
        setup_home_folders(
            home_part,
            log,
            skip_tree=home_tree_on_scratch,
            home_offset_bytes=home_off,
        )
        _, home_n = _parse_partition_path(home_part)
        _finalize_home_volume(
            disk_number, home_n, log,
            home_offset_bytes=home_off,
            home_size_bytes=home_size,
            fattools_in_place=home_tree_on_scratch,
        )
        _suppress_automount_volumes(
            disk_number, log, hide_boot=True, keep_home_letter=True, home_partition=home_n
        )

        step(
            100,
            "Operazione completata con successo\n"
            "La chiavetta Quelo Office è pronta. Rimuovila in sicurezza e avvia il PC da USB.",
        )
        if log:
            log("PASSO 9/9 — Completato")
            log(lsblk_tree(disk))
    finally:
        _shell_hw_detection_start(log)
        _automount_enable(log)
