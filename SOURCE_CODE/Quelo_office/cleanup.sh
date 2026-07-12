#!/bin/bash
# Pulizia mount live-build. Esegui: sudo ./cleanup.sh

set -euo pipefail

LB_DIR="/var/tmp/quelo-office-live-build"
CHROOT="${LB_DIR}/chroot"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Esegui come root: sudo $0"
  exit 1
fi

quelo_safe_umount() {
  local mp="$1"
  local lb_real mp_real

  lb_real="$(realpath "${LB_DIR}" 2>/dev/null)" || return 0
  mp_real="$(realpath "${mp}" 2>/dev/null)" || return 0
  [[ "${mp_real}" == "${lb_real}/"* ]] || return 0
  mountpoint -q "${mp}" 2>/dev/null || return 0
  echo "umount -l ${mp}"
  umount -l "${mp}" 2>/dev/null || true
}

quelo_cleanup_tree() {
  local base="$1"
  local chroot="${base}/chroot"
  [[ -d "${chroot}" ]] || mount | grep -qF "${base}/" || return 0

  for _ in 1 2 3; do
    for mp in \
      "${chroot}/proc/sys/fs/binfmt_misc" \
      "${chroot}/proc" \
      "${chroot}/sys" \
      "${chroot}/dev/pts" \
      "${chroot}/dev/shm" \
      "${chroot}/dev" \
      "${chroot}/run"
    do
      if mountpoint -q "${mp}" 2>/dev/null; then
        echo "umount -l ${mp}"
        umount -l "${mp}" 2>/dev/null || true
      fi
    done
    sleep 1
  done

  if mount | grep -qF "${base}/"; then
    echo "ATTENZIONE: restano mount sotto ${base}"
    echo "Riavvia il PC e rilancia cleanup."
    return 1
  fi

  rm -rf "${base}"
  echo "Rimosso ${base}"
}

echo "Pulizia build live (sicura)..."

if [[ ! -d "${CHROOT}" ]] && ! mount | grep -qF "${LB_DIR}/"; then
  echo "Niente da pulire in ${LB_DIR}"
  exit 0
fi

quelo_cleanup_tree "${LB_DIR}"
echo "Pulizia completata."
