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

BLOCK_SIZE = 1024 * 1024  # 1 MiB
SECTOR_SIZE = 512
O_DIRECT = getattr(os, "O_DIRECT", 0)


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


def open_device_write(dev_path: str, use_direct: bool) -> tuple[int, bool]:
    flags = os.O_WRONLY
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


def write_iso(iso_path: str, dev_path: str, use_direct: bool = True, progress_callback=None) -> int:
    total = os.path.getsize(iso_path)
    dev_name = os.path.basename(dev_path)
    stat_path = f"/sys/block/{dev_name}/stat"

    print("")
    print(f"Scrittura ISO su USB: {human_size(total)} totali.")
    if use_direct and O_DIRECT:
        print("Modalita O_DIRECT: scrittura diretta su USB (no flush finale lungo).")
    else:
        print("Modalita buffered (fallback).")
    print("NON rimuovere la USB fino a 'Verifica completata'.")
    print("")

    start_sectors = read_sector_count(stat_path)

    try:
        out_fd, used_direct = open_device_write(dev_path, use_direct)
    except OSError as exc:
        print(f"ERRORE: apertura {dev_path}: {exc}", file=sys.stderr)
        return 1

    if use_direct and not used_direct:
        print("ATTENZIONE: O_DIRECT non disponibile, uso scrittura buffered.")

    aligned = mmap.mmap(-1, BLOCK_SIZE)
    t0 = time.time()
    logical_written = 0

    try:
        with open(iso_path, "rb", buffering=BLOCK_SIZE) as src:
            while True:
                chunk = src.read(BLOCK_SIZE)
                if not chunk:
                    break

                io_len = pad_to_sector(len(chunk))
                aligned.seek(0)
                aligned.write(chunk)
                if io_len > len(chunk):
                    aligned.write(b"\x00" * (io_len - len(chunk)))

                os.write(out_fd, memoryview(aligned)[:io_len])
                logical_written += len(chunk)

                sysfs = read_bytes_written(stat_path, start_sectors)
                current = max(logical_written, sysfs)
                progress_bar(
                    total,
                    current,
                    int(time.time() - t0),
                    "scrittura",
                    progress_callback,
                )
    except OSError as exc:
        print(f"\nERRORE scrittura: {exc}", file=sys.stderr)
        aligned.close()
        os.close(out_fd)
        return 1

    progress_message("Finalizzazione...", newline=True)
    try:
        flush_device(dev_path, out_fd, used_direct)
    except OSError as exc:
        print(f"\nERRORE finalizzazione: {exc}", file=sys.stderr)
        aligned.close()
        os.close(out_fd)
        return 1

    aligned.close()
    os.close(out_fd)
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


def verify_iso(iso_path: str, dev_path: str, progress_callback=None) -> int:
    total = os.path.getsize(iso_path)

    print("")
    print(f"Verifica ISO su USB: {human_size(total)} totali.")
    print("Confronto bit-per-bit ISO sorgente vs dispositivo.")
    print("")

    try:
        dev_fd = os.open(dev_path, os.O_RDONLY)
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
                dev_chunk = os.read(dev_fd, len(chunk))
                if dev_chunk != chunk:
                    stop.set()
                    prog.join()
                    print(
                        f"\nERRORE verifica: differenza a offset {offset} "
                        f"({human_size(offset)})",
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

    if args.verify_only:
        return verify_iso(iso_path, dev_path)

    rc = write_iso(iso_path, dev_path, use_direct=not args.no_direct)
    if rc != 0 or args.write_only:
        return rc

    return verify_iso(iso_path, dev_path)


if __name__ == "__main__":
    raise SystemExit(main())
