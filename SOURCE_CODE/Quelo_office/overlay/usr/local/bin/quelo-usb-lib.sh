#!/bin/bash
# Funzioni condivise USB: export e mount home.

QUELO_HOME_LABEL="QUELO-HOME"
QUELO_PERSIST_LABEL="persistence"

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && set -euo pipefail

quelo_find_live_medium_source() {
  local mp src
  for mp in /run/live/medium /lib/live/mount/medium; do
    if mountpoint -q "${mp}" 2>/dev/null; then
      src=$(findmnt -no SOURCE "${mp}")
      [[ -n "${src}" ]] && { printf '%s\n' "${src}"; return 0; }
    fi
  done
  return 1
}

quelo_resolve_partition() {
  local src="$1"
  local dev real

  if [[ "${src}" == /dev/dm-* ]]; then
    dev=$(dmsetup info -c --noheadings -o name "${src}" 2>/dev/null | awk '{print $1}')
    [[ -n "${dev}" && -e "/dev/mapper/${dev}" ]] && src="/dev/mapper/${dev}"
  fi

  real=$(readlink -f "${src}" 2>/dev/null || echo "${src}")
  if [[ -b "${real}" ]]; then
    printf '%s\n' "${real}"
    return 0
  fi
  return 1
}

quelo_partition_to_disk() {
  local part="$1"
  local pkname

  pkname=$(lsblk -no PKNAME "${part}" 2>/dev/null || true)
  [[ -n "${pkname}" ]] || return 1
  printf '/dev/%s\n' "${pkname}"
}

quelo_disk_is_live_usb() {
  local medium part disk

  medium=$(quelo_find_live_medium_source) || return 1
  part=$(quelo_resolve_partition "${medium}") || return 1
  disk=$(quelo_partition_to_disk "${part}") || return 1
  printf '%s\n' "${disk}|${part}"
}

quelo_export_home_dir() {
  local mnt
  if mountpoint -q /media/quelo-home 2>/dev/null; then
    printf '/media/quelo-home/quelo-export\n'
    return 0
  fi
  if blkid -L "${QUELO_HOME_LABEL}" >/dev/null 2>&1; then
    mnt=$(mktemp -d)
    # Stesse opzioni di quelo-usb-mount-home: exFAT senza fmask/dmask=0000
    # rende i file leggibili/scrivibili solo da root (vedi quel file per i
    # dettagli).
    if mount -L "${QUELO_HOME_LABEL}" -o uid=0,gid=0,fmask=0000,dmask=0000 "${mnt}" 2>/dev/null; then
      printf '%s/quelo-export\n' "${mnt}"
      return 0
    fi
  fi
  printf '/root/quelo-export\n'
}
