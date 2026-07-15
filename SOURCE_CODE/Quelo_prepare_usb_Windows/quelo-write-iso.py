#!/usr/bin/env python3
"""Scrittura e verifica ISO su block device — I/O diretto stile Etcher."""

from __future__ import annotations

import argparse
import mmap
import os
import subprocess
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"
BLOCK_SIZE = 1024 * 1024  # 1 MiB
SECTOR_SIZE = 512
O_DIRECT = getattr(os, "O_DIRECT", 0) if not IS_WINDOWS else 0
O_BINARY = getattr(os, "O_BINARY", 0)

PROGRESS = sys.stderr
BAR_WIDTH = 24


def human_size(n: int) -> str:
    val = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if val < 1024.0 or unit == "GiB":
            if unit == "B":
                return f"{int(val)}B"
            return f"{val:.1f}{unit}"
        val /= 1024.0
    return f"{n}B"


def human_size_compact(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f}G"
    if n >= 1024**2:
        return f"{n / 1024**2:.0f}M"
    if n >= 1024:
        return f"{n / 1024:.0f}K"
    return f"{n}B"


def progress_message(msg: str, *, newline: bool = False) -> None:
    PROGRESS.write(f"\r\033[2K{msg}")
    if newline:
        PROGRESS.write("\n")
    PROGRESS.flush()


def progress_bar(
    total: int,
    current: int,
    elapsed: int,
    label: str,
    progress_callback=None,
) -> None:
    current = min(max(current, 0), total)
    pct = (current * 100 // total) if total else 0
    if progress_callback is not None:
        progress_callback(total, current, elapsed, label)
        return
    filled = pct * BAR_WIDTH // 100
    bar = "#" * filled + "-" * (BAR_WIDTH - filled)
    progress_message(
        f"[{bar}] {pct:3d}%  "
        f"{human_size_compact(current)}/{human_size_compact(total)}  "
        f"{elapsed}s  {label}"
    )


def read_sector_count(stat_path: str) -> int:
    if not os.path.isfile(stat_path):
        return 0
    try:
        with open(stat_path, encoding="ascii") as fh:
            parts = fh.read().split()
        return int(parts[6]) if len(parts) >= 7 else 0
    except (OSError, ValueError):
        return 0


def read_bytes_written(stat_path: str, start_sectors: int) -> int:
    return max(0, (read_sector_count(stat_path) - start_sectors) * SECTOR_SIZE)


def pad_to_sector(size: int) -> int:
    return ((size + SECTOR_SIZE - 1) // SECTOR_SIZE) * SECTOR_SIZE


def _win32_open_physical(dev_path: str, *, readwrite: bool) -> int:
    """Apre PhysicalDrive in esclusiva (niente condivisione con volumi montati)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    access = (GENERIC_READ | GENERIC_WRITE) if readwrite else GENERIC_WRITE
    handle = kernel32.CreateFileW(
        dev_path,
        access,
        0,  # FILE_SHARE_NONE — blocca mount/Explorer sullo stesso disco
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        err = kernel32.GetLastError()
        raise OSError(err, "CreateFileW", dev_path)

    fd_flags = (os.O_RDWR if readwrite else os.O_WRONLY) | O_BINARY
    return msvcrt.open_osfhandle(handle, fd_flags)


def _win32_flush(fd: int) -> None:
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    handle = msvcrt.get_osfhandle(fd)
    os.fsync(fd)
    if not kernel32.FlushFileBuffers(handle):
        raise OSError(kernel32.GetLastError(), "FlushFileBuffers")


def _disk_number_from_path(dev_path: str) -> int | None:
    import re as _re

    m = _re.search(r"PhysicalDrive(\d+)", dev_path, _re.I)
    return int(m.group(1)) if m else None


def _win32_lock_disk_volumes(dev_path: str) -> list:
    """Blocca e smonta OGNI volume sul disco fisico, tenendo aperti gli handle.

    Senza questo Windows scarta le scritture raw sui settori dei volumi
    montati (verifica legge 00). È il metodo usato da Rufus/Win32DiskImager:
    FSCTL_LOCK_VOLUME + FSCTL_DISMOUNT_VOLUME con handle mantenuto aperto
    per tutta la durata di scrittura e verifica.
    """
    import ctypes
    from ctypes import wintypes

    disk_number = _disk_number_from_path(dev_path)
    if disk_number is None:
        return []

    kernel32 = ctypes.windll.kernel32
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
    FSCTL_LOCK_VOLUME = 0x00090018
    FSCTL_DISMOUNT_VOLUME = 0x00090020

    class STORAGE_DEVICE_NUMBER(ctypes.Structure):
        _fields_ = [
            ("DeviceType", wintypes.DWORD),
            ("DeviceNumber", wintypes.DWORD),
            ("PartitionNumber", wintypes.DWORD),
        ]

    handles: list = []
    bitmask = kernel32.GetLogicalDrives()
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        vol_path = f"\\\\.\\{letter}:"
        h = kernel32.CreateFileW(
            vol_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if h == INVALID_HANDLE_VALUE:
            continue

        sdn = STORAGE_DEVICE_NUMBER()
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            h,
            IOCTL_STORAGE_GET_DEVICE_NUMBER,
            None,
            0,
            ctypes.byref(sdn),
            ctypes.sizeof(sdn),
            ctypes.byref(returned),
            None,
        )
        if not ok or sdn.DeviceNumber != disk_number:
            kernel32.CloseHandle(h)
            continue

        # Lock + dismount; se il lock fallisce (volume occupato) provo comunque
        # il dismount, che rende i settori scrivibili.
        for _ in range(20):
            if kernel32.DeviceIoControl(
                h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(returned), None
            ):
                break
            time.sleep(0.5)
        kernel32.DeviceIoControl(
            h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(returned), None
        )
        print(f"Volume {letter}: bloccato e smontato per scrittura raw.", file=sys.stderr)
        handles.append(h)

    return handles


def _win32_unlock_disk_volumes(handles: list) -> None:
    if not handles:
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    FSCTL_UNLOCK_VOLUME = 0x0009001C
    returned = ctypes.c_ulong(0)
    for h in handles:
        try:
            kernel32.DeviceIoControl(
                h, FSCTL_UNLOCK_VOLUME, None, 0, None, 0, ctypes.byref(returned), None
            )
            kernel32.CloseHandle(h)
        except OSError:
            pass


def open_device_write(dev_path: str, use_direct: bool) -> tuple[int, bool]:
    flags = os.O_WRONLY | O_BINARY
    if use_direct and O_DIRECT:
        try:
            return os.open(dev_path, flags | O_DIRECT), True
        except OSError:
            pass
    return os.open(dev_path, flags), False


def flush_device(dev_path: str, out_fd: int, used_direct: bool) -> None:
    if used_direct:
        subprocess.run(
            ["blockdev", "--flushbufs", dev_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.fsync(out_fd)


def _read_full(fd: int, size: int) -> bytes:
    """Legge esattamente size byte (gestisce letture parziali su Windows)."""
    buf = b""
    while len(buf) < size:
        part = os.read(fd, size - len(buf))
        if not part:
            break
        buf += part
    return buf


def write_iso(
    iso_path: str,
    dev_path: str,
    use_direct: bool = True,
    progress_callback=None,
    *,
    verify_after: bool = False,
) -> int:
    total = os.path.getsize(iso_path)
    stat_path = None
    start_sectors = 0
    if not IS_WINDOWS:
        dev_name = os.path.basename(dev_path)
        stat_path = f"/sys/block/{dev_name}/stat"
        start_sectors = read_sector_count(stat_path)

    if IS_WINDOWS:
        use_direct = False

    open_flags = os.O_WRONLY | O_BINARY
    if IS_WINDOWS and verify_after:
        open_flags = os.O_RDWR | O_BINARY

    locked_handles: list = []
    if IS_WINDOWS:
        locked_handles = _win32_lock_disk_volumes(dev_path)

    try:
        if IS_WINDOWS:
            out_fd = _win32_open_physical(dev_path, readwrite=verify_after)
        else:
            out_fd = os.open(dev_path, open_flags)
        used_direct = False
    except OSError as exc:
        _win32_unlock_disk_volumes(locked_handles)
        print(f"ERRORE: apertura {dev_path}: {exc}", file=sys.stderr)
        return 1

    if use_direct and not used_direct and not IS_WINDOWS:
        print("ATTENZIONE: O_DIRECT non disponibile, uso scrittura buffered.")

    aligned: mmap.mmap | None = None
    if not IS_WINDOWS:
        aligned = mmap.mmap(-1, BLOCK_SIZE)
    t0 = time.time()
    logical_written = 0

    try:
        with open(iso_path, "rb", buffering=BLOCK_SIZE) as src:
            while True:
                chunk = src.read(BLOCK_SIZE)
                if not chunk:
                    break

                if IS_WINDOWS:
                    os.write(out_fd, chunk)
                else:
                    assert aligned is not None
                    io_len = pad_to_sector(len(chunk))
                    aligned.seek(0)
                    aligned.write(chunk)
                    if io_len > len(chunk):
                        aligned.write(b"\x00" * (io_len - len(chunk)))
                    os.write(out_fd, memoryview(aligned)[:io_len])
                logical_written += len(chunk)

                if stat_path:
                    sysfs = read_bytes_written(stat_path, start_sectors)
                    current = max(logical_written, sysfs)
                else:
                    current = logical_written
                progress_bar(
                    total,
                    current,
                    int(time.time() - t0),
                    "scrittura",
                    progress_callback,
                )
    except OSError as exc:
        print(f"\nERRORE scrittura: {exc}", file=sys.stderr)
        if aligned is not None:
            aligned.close()
        os.close(out_fd)
        _win32_unlock_disk_volumes(locked_handles)
        return 1

    progress_message("Finalizzazione...", newline=True)
    try:
        if IS_WINDOWS:
            _win32_flush(out_fd)
        else:
            os.fsync(out_fd)
    except OSError as exc:
        print(f"\nERRORE finalizzazione: {exc}", file=sys.stderr)
        if aligned is not None:
            aligned.close()
        os.close(out_fd)
        _win32_unlock_disk_volumes(locked_handles)
        return 1

    if verify_after:
        rc = verify_iso_on_fd(iso_path, out_fd, progress_callback=progress_callback)
        if rc != 0:
            if aligned is not None:
                aligned.close()
            os.close(out_fd)
            _win32_unlock_disk_volumes(locked_handles)
            return rc

    if aligned is not None:
        aligned.close()
    os.close(out_fd)
    _win32_unlock_disk_volumes(locked_handles)
    print("Scrittura completata.")
    return 0


def progress_loop_verify(
    total: int,
    stop: threading.Event,
    state: dict[str, int],
    progress_callback=None,
) -> None:
    t0 = time.time()
    while not stop.is_set():
        progress_bar(
            total,
            state.get("verified", 0),
            int(time.time() - t0),
            "verifica",
            progress_callback,
        )
        stop.wait(0.25)
    if progress_callback is None:
        progress_message("", newline=True)


def verify_iso_on_fd(
    iso_path: str,
    dev_fd: int,
    progress_callback=None,
) -> int:
    """Verifica ISO riusando l'handle aperto (necessario su Windows)."""
    total = os.path.getsize(iso_path)

    print("")
    print(f"Verifica ISO su USB: {human_size(total)} totali.")
    print("Confronto bit-per-bit ISO sorgente vs dispositivo.")
    print("")

    try:
        os.lseek(dev_fd, 0, os.SEEK_SET)
    except OSError as exc:
        print(f"ERRORE verifica (seek): {exc}", file=sys.stderr)
        return 1

    state: dict[str, int] = {"verified": 0}
    stop = threading.Event()
    prog = threading.Thread(
        target=progress_loop_verify,
        args=(total, stop, state, progress_callback),
        daemon=True,
    )
    prog.start()

    try:
        with open(iso_path, "rb", buffering=BLOCK_SIZE) as src:
            offset = 0
            while offset < total:
                chunk = src.read(BLOCK_SIZE)
                if not chunk:
                    break
                dev_chunk = _read_full(dev_fd, len(chunk))
                if dev_chunk != chunk:
                    stop.set()
                    prog.join()
                    detail = ""
                    if len(dev_chunk) != len(chunk):
                        detail = f" (letti {len(dev_chunk)}/{len(chunk)} byte)"
                    else:
                        for i, (a, b) in enumerate(zip(dev_chunk, chunk)):
                            if a != b:
                                detail = (
                                    f" (primo byte diverso @+{i}: "
                                    f"ISO {a:02x} vs USB {b:02x})"
                                )
                                break
                    print(
                        f"\nERRORE verifica: differenza a offset {offset} "
                        f"({human_size(offset)}){detail}",
                        file=sys.stderr,
                    )
                    return 1
                offset += len(chunk)
                state["verified"] = offset
    except OSError as exc:
        stop.set()
        prog.join()
        print(f"ERRORE verifica: {exc}", file=sys.stderr)
        return 1

    stop.set()
    prog.join()
    print("Verifica completata: ISO e dispositivo coincidono.")
    return 0


def verify_iso(iso_path: str, dev_path: str, progress_callback=None) -> int:
    total = os.path.getsize(iso_path)

    print("")
    print(f"Verifica ISO su USB: {human_size(total)} totali.")
    print("Confronto bit-per-bit ISO sorgente vs dispositivo.")
    print("")

    try:
        dev_fd = os.open(dev_path, os.O_RDONLY | O_BINARY)
    except OSError as exc:
        print(f"ERRORE: lettura {dev_path}: {exc}", file=sys.stderr)
        return 1

    state: dict[str, int] = {"verified": 0}
    stop = threading.Event()
    prog = threading.Thread(
        target=progress_loop_verify,
        args=(total, stop, state, progress_callback),
        daemon=True,
    )
    prog.start()

    try:
        with open(iso_path, "rb", buffering=BLOCK_SIZE) as src:
            offset = 0
            while offset < total:
                chunk = src.read(BLOCK_SIZE)
                if not chunk:
                    break
                dev_chunk = _read_full(dev_fd, len(chunk))
                if dev_chunk != chunk:
                    stop.set()
                    prog.join()
                    detail = ""
                    if len(dev_chunk) != len(chunk):
                        detail = f" (letti {len(dev_chunk)}/{len(chunk)} byte)"
                    else:
                        for i, (a, b) in enumerate(zip(dev_chunk, chunk)):
                            if a != b:
                                detail = (
                                    f" (primo byte diverso @+{i}: "
                                    f"ISO {a:02x} vs USB {b:02x})"
                                )
                                break
                    print(
                        f"\nERRORE verifica: differenza a offset {offset} "
                        f"({human_size(offset)}){detail}",
                        file=sys.stderr,
                    )
                    os.close(dev_fd)
                    return 1
                offset += len(chunk)
                state["verified"] = offset
    except OSError as exc:
        stop.set()
        prog.join()
        print(f"ERRORE verifica: {exc}", file=sys.stderr)
        os.close(dev_fd)
        return 1

    stop.set()
    prog.join()
    os.close(dev_fd)
    print("Verifica completata: ISO e dispositivo coincidono.")
    return 0


def write_file_to_device(
    file_path: str,
    dev_path: str,
    progress_callback=None,
    *,
    offset_bytes: int = 0,
) -> int:
    """Copia un file su dispositivo raw (PhysicalDrive). offset_bytes per partizioni."""
    total = os.path.getsize(file_path)
    use_direct = not IS_WINDOWS

    try:
        out_fd, used_direct = open_device_write(dev_path, use_direct)
    except OSError as exc:
        print(f"ERRORE: apertura {dev_path}: {exc}", file=sys.stderr)
        return 1

    if offset_bytes:
        try:
            os.lseek(out_fd, offset_bytes, os.SEEK_SET)
        except OSError as exc:
            print(
                f"ERRORE: seek {dev_path} @ {offset_bytes}: {exc}",
                file=sys.stderr,
            )
            os.close(out_fd)
            return 1

    if IS_WINDOWS:
        buf = bytearray(BLOCK_SIZE)
    else:
        aligned = mmap.mmap(-1, BLOCK_SIZE)
    t0 = time.time()
    written = 0

    try:
        with open(file_path, "rb", buffering=BLOCK_SIZE) as src:
            while True:
                chunk = src.read(BLOCK_SIZE)
                if not chunk:
                    break

                io_len = pad_to_sector(len(chunk))
                if IS_WINDOWS:
                    view = memoryview(buf)[:io_len]
                    view[: len(chunk)] = chunk
                    if io_len > len(chunk):
                        view[len(chunk) :] = b"\x00" * (io_len - len(chunk))
                    os.write(out_fd, view)
                else:
                    aligned.seek(0)
                    aligned.write(chunk)
                    if io_len > len(chunk):
                        aligned.write(b"\x00" * (io_len - len(chunk)))
                    os.write(out_fd, memoryview(aligned)[:io_len])
                written += len(chunk)
                progress_bar(
                    total,
                    written,
                    int(time.time() - t0),
                    "scrittura",
                    progress_callback,
                )
    except OSError as exc:
        print(f"\nERRORE copia file: {exc}", file=sys.stderr)
        if not IS_WINDOWS:
            aligned.close()
        os.close(out_fd)
        return 1

    try:
        flush_device(dev_path, out_fd, used_direct)
    except OSError as exc:
        print(f"\nERRORE finalizzazione: {exc}", file=sys.stderr)
        if not IS_WINDOWS:
            aligned.close()
        os.close(out_fd)
        return 1

    if not IS_WINDOWS:
        aligned.close()
    os.close(out_fd)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrittura/verifica ISO su block device")
    parser.add_argument("iso", help="Percorso file ISO")
    parser.add_argument("device", help="Dispositivo block, es. /dev/sdb")
    parser.add_argument(
        "--write-only",
        action="store_true",
        help="Solo scrittura, senza verifica",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Solo verifica (ISO gia scritta)",
    )
    parser.add_argument(
        "--no-direct",
        action="store_true",
        help="Disabilita O_DIRECT (scrittura buffered)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    iso_path, dev_path = args.iso, args.device

    if not os.path.isfile(iso_path):
        print(f"ERRORE: ISO non trovata: {iso_path}", file=sys.stderr)
        return 1
    if not os.path.exists(dev_path):
        print(f"ERRORE: dispositivo non trovato: {dev_path}", file=sys.stderr)
        return 1

    use_direct = not args.no_direct and not IS_WINDOWS

    if args.verify_only:
        return verify_iso(iso_path, dev_path)

    rc = write_iso(iso_path, dev_path, use_direct=use_direct)
    if rc != 0 or args.write_only:
        return rc

    return verify_iso(iso_path, dev_path)


if __name__ == "__main__":
    raise SystemExit(main())
