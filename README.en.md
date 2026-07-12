# Quelo Office

[Italiano](README.md) · **English**

**Quelo Office** is a live Linux distribution designed to run from a USB stick: a small, portable office environment in Italian, ready to use on almost any PC.

Inspired by Corrado Guzzanti’s «Quelo» character, it combines the reliability of **Debian GNU/Linux** with a lightweight desktop (**LXQt + Openbox**), without the complexity of a traditional installation.

Current alpha release: **0.71** (`ISO/Quelo_Office-0.71-alpha.iso`).

Website: **https://alby-quelo.github.io/quelo-office/en/**

![Quelo Office 0.71 alpha desktop](docs/screenshots/01-desktop.png)


## What it is for

Quelo Office is aimed at people who want to:

- carry a **full office setup** (documents, browser, web mail, printing, scanning);
- work on **multiple PCs** with the same USB stick, without touching internal disks;
- keep **personal files readable on Windows and macOS** (exFAT home partition);
- save **only the settings that matter** (network, printers, desktop, audio, display, Firefox…), without turning the live system into an opaque, hard-to-update installation.


## Main features

### System and desktop

- Based on **Debian sid** (live-build), **Italian** locale, **Europe/Rome** timezone.
- Boots straight to the desktop: autologin, **startx**, and an **LXQt** session with **Openbox** as the window manager.
- **Two LXQt panels**:
  - top: version, RAM usage, home free space, clock;
  - bottom: application menu, taskbar, system tray (network, Bluetooth, volume), trash, power off/reboot.
- Custom menu with the Quelo logo; adjustable font size for the menu and bottom panel.
- Display layout and multi-monitor setup via **lxrandr** (`quelo-lxrandr` wrapper).

### Included applications

| Area | Software |
|------|----------|
| Office | LibreOffice (Italian UI and spell checker) |
| Web | Firefox ESR (Italian) |
| Text / PDF | Mousepad, Zathura |
| Files | pcmanfm-qt (file manager and desktop) |
| Images | lximage-qt (viewer), mtPaint (editing) |
| Multimedia | SMPlayer (video), Audacious (audio) |
| Utilities | KCalc, lxterminal, Xarchiver, SMB4K, screenshot tool |
| Print / scan | CUPS, broad driver set, Simple Scan |
| Network | NetworkManager, Wi‑Fi/LAN firmware, mobile broadband |
| Bluetooth | Blueman |

**Microsoft TrueType** fonts (Arial, Times, Verdana…) for Office document compatibility.

### Hardware and connectivity

- Broad **network firmware** coverage (Intel, Realtek, Broadcom, Mediatek…).
- **Printers** over network and USB (Avahi/mDNS, ipp-usb, many drivers).
- **Scanners** via SANE (including airscan).
- **Bluetooth** with a panel applet.
- **Network shares** (Samba/CIFS) via SMB4K.

### Live behaviour

- Every boot starts from a **clean ISO image**: no automatic Debian-style live overlay silently changing `/usr` or installed packages.
- Caches and temp data live in **RAM** (`/tmp`, apt cache, etc.) — sessions stay lean.
- **Selective session save**: on reboot or shutdown you choose what to keep among network, printers, Bluetooth, desktop, audio, display, and Firefox. A similar dialog restores settings at startup.
- User data (documents, downloads, pictures…) lives on the **QUELO-HOME** **exFAT** partition, auto-mounted and linked to standard folders (Desktop, Documents, Downloads…).


## Screenshots

### Desktop

Top panel: version, RAM, home free space, clock. Bottom panel: Quelo menu, taskbar, network, Bluetooth, volume, trash, power off.

![Quelo Office desktop](docs/screenshots/01-desktop.png)

### Restore at startup

After each boot, a dialog asks which saved settings to restore from the persistence partition. You can deselect items you do not need (useful when changing monitor or network).

![Restore selection](docs/screenshots/02-restore-prompt.png)

![Restore in progress](docs/screenshots/03-restore-progress.png)

![Restore complete](docs/screenshots/04-restore-complete.png)

### Save on shutdown

From the **Power** button (reboot or shutdown) you can selectively save network, printers, Bluetooth, desktop, audio, display, and Firefox settings.

![Save selection](docs/screenshots/05-save-prompt.png)

![Save in progress](docs/screenshots/06-save-progress.png)

![Save complete](docs/screenshots/07-save-complete.png)


## USB stick layout

After running `prepare-usb.sh`, the stick has three logical areas:

```
┌─────────────────────────────────────────────────────────┐
│  Partition 1–2   │  Live ISO (read-only at boot)       │
├──────────────────┼──────────────────────────────────────┤
│  Partition 3     │  ext4 «persistence» — Linux settings  │
│                  │  (invisible to Windows/macOS)        │
├──────────────────┼──────────────────────────────────────┤
│  Partition 4     │  exFAT «QUELO-HOME» — your files     │
│                  │  (readable everywhere)               │
└──────────────────┴──────────────────────────────────────┘
```

You choose the persistence partition size during preparation (e.g. 128 / 256 / 512 MB or more).


## How to use it

### 1. Download the ISO

Official file: **`ISO/Quelo_Office-0.71-alpha.iso`**

This is the only image intended for public distribution.

### 2. Prepare the USB stick

**Important:** preparation must be done on a **host PC**, **never** while booted into Quelo Office from the same stick you are writing to.

```bash
sudo SOURCE_CODE/Quelo_prepare_usb/prepare-usb.sh
```

Host prerequisites: `e2fsprogs`, `exfatprogs`, `util-linux` (fdisk, dd, wipefs…).

The script walks through nine steps with pauses and a safety double confirmation (`SI SCRIVI`).

### 3. Boot from BIOS/UEFI

Select USB boot. On first login the desktop is ready; if you created QUELO-HOME, your personal folders are already linked.

### 4. Shutdown and session

The **Power** button on the panel offers reboot or shutdown and, optionally, **selective saving** of settings before exit.


## Why a separate script for USB preparation?

The project initially had a **first-boot wizard** inside the live system that partitioned the USB while booted from it. That was **removed** and replaced by **`prepare-usb.sh`**, which runs only on the host. Reasons:

1. **Safety** — Operations like `dd`, `fdisk`, and `mkfs` are destructive if aimed at the wrong disk. On the host, with checks for removable media, double confirmation, and an explicit ban on writing to the boot disk, the risk drops sharply. From a live session **on the same USB**, those checks are much weaker.

2. **Reliability** — Resizing and formatting the disk you boot from is technically fragile (partitions in use, caching, kernel races). On the host PC the USB is a free external device: writing the ISO and creating partitions is predictable.

3. **One ISO for everyone** — The live image stays **identical and universal**: download, verify, share. Each user prepares **their own** stick (persistence size, home folders, stick capacity) **afterwards**, with no ISO variants.

4. **Tool choice** — You can write the ISO with **Balena Etcher** or similar and use the script only to add persistence and QUELO-HOME. Or do everything in one go with `prepare-usb.sh` (write + partitions + format).

5. **Separation of concerns** — The **ISO** defines the Quelo Office system; the **USB script** defines *how* you put it on physical media. Updating the distro (new ISO) does not force partitioning logic back inside the live system.

In short: **the live system is for working; the host PC is for preparing the stick safely.**


## Development and source code

Build code and USB scripts live under **`SOURCE_CODE/`**:

| Directory | Contents |
|-----------|----------|
| `Quelo_office/` | ISO build (live-build, overlay, hooks) |
| `Quelo_prepare_usb/` | `prepare-usb.sh`, `quelo-write-iso.py` |

From the live system, `quelo-export` (in `QUELO-HOME/quelo-export/`) collects packages and configuration added during testing, so they can be merged into the sources and baked into the next ISO.

Legal documents: **`LICENSE.TXT`**, **`CREDITS.TXT`** (repository root).


## License

Original Quelo Office work is released under **Creative Commons BY-NC 4.0**. Non-commercial and educational use is allowed with attribution; commercial use requires written permission from **Alberto Frosio** (`alby@gnumerica.org`).

Software included in the ISO remains under its own licenses (Debian, LibreOffice, Firefox, etc.) — see `LICENSE.TXT` and `CREDITS.TXT`.
