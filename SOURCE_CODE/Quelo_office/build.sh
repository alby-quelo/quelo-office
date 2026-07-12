#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true

SORGENTI="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "${SORGENTI}/../.." && pwd)"
LB_DIR="/var/tmp/quelo-office-live-build"
LB_CACHE="/var/tmp/quelo-office-lb-cache"
MSFONT_CACHE="/var/tmp/quelo-office-mscorefonts-cache"
MSFONT_URL="https://downloads.sourceforge.net/project/corefonts/the%20fonts/final"
MSFONT_EXES=(
  andale32.exe arial32.exe arialb32.exe comic32.exe courie32.exe
  georgi32.exe impact32.exe times32.exe trebuc32.exe verdan32.exe webdin32.exe
)
VERSION_FILE="${SORGENTI}/VERSION"
LOG="${PROJECT}/.build/build.log"
BUILD_USER="${SUDO_USER:-${USER:-root}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Esegui come root: sudo $0"
  exit 1
fi

if [[ ! -f "${VERSION_FILE}" ]]; then
  echo "0.01" >"${VERSION_FILE}"
fi

VERSION="$(tr -d '[:space:]' <"${VERSION_FILE}")"
OUTPUT_ISO="${PROJECT}/ISO/Quelo_Office-${VERSION}-alpha.iso"

quelo_safe_umount() {
  local mp="$1"
  local lb_real mp_real

  lb_real="$(realpath "${LB_DIR}" 2>/dev/null)" || return 0
  mp_real="$(realpath "${mp}" 2>/dev/null)" || return 0
  [[ "${mp_real}" == "${lb_real}/"* ]] || return 0
  mountpoint -q "${mp}" 2>/dev/null || return 0
  umount -l "${mp}" 2>/dev/null || true
}

quelo_cleanup_build() {
  local chroot="${LB_DIR}/chroot"
  [[ -d "${chroot}" ]] || return 0

  echo "Smonto mount live-build (solo lazy umount, sicuro)..."
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
      quelo_safe_umount "${mp}"
    done
    sleep 1
  done
}

quelo_build_still_mounted() {
  mount | grep -qF "${LB_DIR}/"
}

quelo_preserve_lb_cache() {
  local saved=""
  if [[ -d "${LB_DIR}/cache" ]]; then
    saved="$(mktemp -d "${LB_CACHE}/saved.XXXXXX")"
    mv "${LB_DIR}/cache" "${saved}/cache"
    echo "${saved}"
  fi
}

quelo_restore_lb_cache() {
  local saved="$1"
  if [[ -n "${saved}" && -d "${saved}/cache" ]]; then
    rm -rf "${LB_DIR}/cache"
    mv "${saved}/cache" "${LB_DIR}/cache"
    rm -rf "${saved}"
    return
  fi
  if [[ -d "${LB_CACHE}/cache" ]]; then
    mkdir -p "${LB_DIR}"
    rm -rf "${LB_DIR}/cache"
    cp -a "${LB_CACHE}/cache" "${LB_DIR}/cache"
  fi
}

quelo_save_lb_cache() {
  [[ -d "${LB_DIR}/cache" ]] || return 0
  rm -rf "${LB_CACHE}/cache"
  mkdir -p "${LB_CACHE}"
  cp -a "${LB_DIR}/cache" "${LB_CACHE}/cache"
}

quelo_ensure_mscorefonts_cache() {
  local exe
  mkdir -p "${MSFONT_CACHE}"
  for exe in "${MSFONT_EXES[@]}"; do
    [[ -s "${MSFONT_CACHE}/${exe}" ]] && continue
    echo "Cache mscorefonts: scarico ${exe}..."
    if ! wget -q --continue --tries=3 -O "${MSFONT_CACHE}/${exe}.part" "${MSFONT_URL}/${exe}"; then
      echo "ERRORE: download ${exe} fallito"
      rm -f "${MSFONT_CACHE}/${exe}.part"
      return 1
    fi
    mv "${MSFONT_CACHE}/${exe}.part" "${MSFONT_CACHE}/${exe}"
  done
  echo "Cache mscorefonts OK (${#MSFONT_EXES[@]} file, ${MSFONT_CACHE})"
}

quelo_install_mscorefonts_includes() {
  local dest="${LB_DIR}/config/includes.chroot_before_packages/var/lib/msttcorefonts/cache"
  quelo_ensure_mscorefonts_cache
  mkdir -p "${dest}"
  cp -an "${MSFONT_CACHE}/". "${dest}/"
}

mkdir -p "${PROJECT}/.build" "${PROJECT}/ISO" "${MSFONT_CACHE}" "${LB_CACHE}"
exec > >(tee -a "${LOG}") 2>&1

echo "=== BUILD $(date -Iseconds) versione ${VERSION} ==="
echo "Build dir: ${LB_DIR}"
echo "Cache live-build: ${LB_CACHE}"
echo "Cache mscorefonts: ${MSFONT_CACHE}"

missing=()
for cmd in lb debootstrap mksquashfs xorriso wget; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done

if ((${#missing[@]})); then
  apt-get update
  apt-get install -y live-build debootstrap squashfs-tools xorriso rsync wget
fi

quelo_cleanup_build
if quelo_build_still_mounted; then
  echo ""
  echo "ERRORE: mount live-build ancora attivi."
  echo "Esegui dopo un riavvio, oppure: sudo ${SORGENTI}/cleanup.sh"
  exit 1
fi

CACHE_SAVED=""
if [[ "${QUelo_FULL_REBUILD:-0}" != "1" ]]; then
  CACHE_SAVED="$(quelo_preserve_lb_cache || true)"
fi

rm -rf "${LB_DIR}"
mkdir -p "${LB_DIR}/config/includes.chroot" \
  "${LB_DIR}/config/includes.binary" \
  "${LB_DIR}/config/hooks/normal"

if [[ "${QUelo_FULL_REBUILD:-0}" != "1" ]]; then
  quelo_restore_lb_cache "${CACHE_SAVED}"
fi

# Pulizia cache "pericolosa": non usiamo mai --cache-stages rootfs (vedi
# commento sopra "lb config" piu' sotto), ma quelo_preserve/restore_lb_cache
# copiano cache/ in blocco, quindi un residuo di cache/binary_rootfs salvato
# da build passate (quando "rootfs" era ancora nelle cache-stages)
# rientrerebbe comunque e reinnescherebbe il bug gia' risolto (binary_chroot
# lo usa come segnale per saltare l'annidamento chroot/chroot, poi
# mksquashfs non trova piu' "chroot" e la build fallisce). cache/bootstrap
# invece VA TENUTO: e' richiesto dal processo stesso di live-build (vedi
# /usr/share/live/build/functions/configuration.sh, "bootstrap caching
# currently required for process to work"), sia da binary_chroot che da
# installer_chroot per il loro giro di annidamento chroot/chroot.
rm -rf "${LB_DIR}/cache/binary_rootfs"
rm -rf "${LB_CACHE}/cache/binary_rootfs"

cd "${LB_DIR}"

# ATTENZIONE: NON aggiungere "rootfs" a --cache-stages qui sotto.
# live-build (binary_rootfs) con "rootfs" tra le cache-stages, se trova
# cache/binary_rootfs, copia la squashfs cachata dal primo run e basta
# (exit 0 prima di rigenerarla): TUTTE le build successive spedirebbero
# una squashfs congelata, ignorando hook/overlay/pacchetti nuovi.
# bootstrap+chroot vanno invece tenuti: "bootstrap" e' richiesto dal
# processo stesso di live-build (binary_chroot/installer_chroot ci
# annidano dentro il chroot vero per lanciare mksquashfs/copiare i file
# del d-i), "chroot" cachea solo i pacchetti apt scaricati.
# NON toccare --build-with-chroot (default true): con false, step come
# binary_syslinux richiedono tool installati sull'HOST invece che nel
# chroot target (es. serve syslinux-common sull'host).
#
lb config \
  --distribution sid \
  --archive-areas "main contrib non-free non-free-firmware" \
  --linux-flavours amd64 \
  --linux-packages "linux-image" \
  --cache true \
  --cache-indices true \
  --cache-packages true \
  --cache-stages bootstrap,chroot \
  --debconf-frontend noninteractive \
  --apt-options "-y" \
  --bootappend-live "boot=live components username=root locales=it_IT.UTF-8 console=tty1 modprobe.blacklist=floppy floppy.allowed_drive_mask=0" \
  --bootappend-live-failsafe "boot=live components username=root locales=it_IT.UTF-8 console=tty1 nomodeset modprobe.blacklist=floppy floppy.allowed_drive_mask=0" \
  --debian-installer none \
  --iso-application "Quelo Office ${VERSION} alpha" \
  --iso-volume "QUELO-OFFICE-${VERSION}" \
  --memtest none \
  --win32-loader false \
  --apt-recommends false \
  --security false \
  --updates false \
  --backports false \
  --firmware-binary false \
  --firmware-chroot false \
  --initramfs "live-boot" \
  --initramfs-compression gzip \
  --chroot-squashfs-compression-type zstd \
  --initsystem systemd

cp -a "${SORGENTI}/overlay/." "${LB_DIR}/config/includes.chroot/"
mkdir -p "${LB_DIR}/config/includes.chroot/etc"
echo "${VERSION}" >"${LB_DIR}/config/includes.chroot/etc/quelo-office-version"
echo "${VERSION}" >"${LB_DIR}/config/includes.binary/quelo-version"

for hook in "${SORGENTI}/hooks/"*.chroot "${SORGENTI}/hooks/"*.binary; do
  [[ -f "${hook}" ]] || continue
  cp "${hook}" "${LB_DIR}/config/hooks/normal/"
  chmod +x "${LB_DIR}/config/hooks/normal/$(basename "${hook}")"
done

if [[ -d "${SORGENTI}/preseed" ]]; then
  mkdir -p "${LB_DIR}/config/preseed"
  for preseed in "${SORGENTI}/preseed/"*.cfg*; do
    [[ -f "${preseed}" ]] || continue
    cp "${preseed}" "${LB_DIR}/config/preseed/"
  done
fi

quelo_install_mscorefonts_includes

# Solo pass "live": evita doppia coda install+live (live-build installa due volte *.list.chroot)
if [[ -f "${SORGENTI}/packages/extra.list.chroot" ]]; then
  cat "${SORGENTI}/packages/extra.list.chroot" >>"${LB_DIR}/config/package-lists/quelo.list.chroot_live"
fi

echo "Output: ${OUTPUT_ISO}"
if ! lb build; then
  echo ""
  echo "ERRORE: lb build fallita."
  echo "Log: ${LOG}"
  echo "Pulizia: sudo ${SORGENTI}/cleanup.sh"
  exit 1
fi

ISO="$(find "${LB_DIR}" -maxdepth 1 -name '*.iso' -type f -print -quit 2>/dev/null || true)"

if [[ -z "${ISO}" || ! -f "${ISO}" ]]; then
  echo "ERRORE: ISO non generata. Log: ${LOG}"
  exit 1
fi

NEXT_VERSION="$(awk -v v="${VERSION}" 'BEGIN { printf "%.2f", v + 0.01 }')"

cp -f "${ISO}" "${OUTPUT_ISO}"
chown "${BUILD_USER}:${BUILD_USER}" "${OUTPUT_ISO}" 2>/dev/null || true

quelo_save_lb_cache

echo "${NEXT_VERSION}" >"${VERSION_FILE}"
chown "${BUILD_USER}:${BUILD_USER}" "${VERSION_FILE}" 2>/dev/null || true

echo ""
echo "ISO PRONTA: ${OUTPUT_ISO}"
echo "Prossima versione: ${NEXT_VERSION}"
