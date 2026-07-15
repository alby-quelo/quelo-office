#!/bin/bash
# Scarica installer Python (32 bit) e runtime mke2fs per pacchetto Windows OFFLINE.
# Eseguire prima di build-archives.sh (serve rete solo sul PC di build).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLERS="${SCRIPT_DIR}/installers"
TOOLS="${SCRIPT_DIR}/tools/e2fsprogs"
PYTHON_VER="3.9.13"
PYTHON_EXE="python-${PYTHON_VER}.exe"
PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VER}/${PYTHON_EXE}"
# Archivio Cygwin x86 congelato (ultimo supporto 32 bit, nov 2022)
CYG_BASE="http://ctm.crouchingtigerhiddenfruitbat.org/pub/cygwin/circa/2022/11/23/063457/x86/release"

# mke2fs 32 bit — stesso ABI di Python embed 32 bit (retrocompatibilità Win7+).
CYG_PACKAGES=(
  "e2fsprogs/e2fsprogs-1.42.12-2.tar.xz"
  "e2fsprogs/libext2fs2/libext2fs2-1.42.12-2.tar.xz"
  "e2fsprogs/libe2p2/libe2p2-1.42.12-2.tar.xz"
  "e2fsprogs/libcom_err2/libcom_err2-1.42.12-2.tar.xz"
  "util-linux/libblkid1/libblkid1-2.33.1-1.tar.xz"
  "util-linux/libuuid1/libuuid1-2.33.1-1.tar.xz"
  "gettext/libintl8/libintl8-0.19.7-1.tar.xz"
  "libiconv/libiconv2/libiconv2-1.14-3.tar.xz"
  "gcc/libgcc1/libgcc1-9.3.0-1.tar.xz"
  "cygwin/cygwin-3.3.6-1.tar.xz"
)

REQUIRED_DLLS=(
  cygwin1.dll
  cygcom_err-2.dll
  cyge2p-2.dll
  cygext2fs-2.dll
  cygblkid-1.dll
  cygintl-8.dll
  cyguuid-1.dll
  cygiconv-2.dll
  cyggcc_s-1.dll
)

mkdir -p "${INSTALLERS}" "${TOOLS}"

rm -f "${INSTALLERS}"/python-*-amd64.exe "${INSTALLERS}"/python-*-win_amd64.exe 2>/dev/null || true

if [[ ! -f "${INSTALLERS}/${PYTHON_EXE}" ]]; then
  echo "Scarico ${PYTHON_EXE} (32 bit)..."
  curl -fsSL -o "${INSTALLERS}/${PYTHON_EXE}" "${PYTHON_URL}"
else
  echo "OK: ${INSTALLERS}/${PYTHON_EXE}"
fi

_verify_mke2fs_deps() {
  local exe="${TOOLS}/mke2fs.exe"
  [[ -f "${exe}" ]] || return 1
  for dll in "${REQUIRED_DLLS[@]}"; do
    [[ -f "${TOOLS}/${dll}" ]] || return 1
  done
  if file -b "${exe}" | grep -q 'PE32+'; then
    return 1
  fi
  if ! file -b "${exe}" | grep -q 'PE32'; then
    return 1
  fi
  if ! objdump -p "${exe}" >/dev/null 2>&1; then
    return 1
  fi
  local missing
  missing="$(
    python3 - "${TOOLS}" "${exe}" <<'PY'
import re, subprocess, sys
from pathlib import Path

tools = Path(sys.argv[1])
exe = Path(sys.argv[2])
need = set()
for path in [exe, *tools.glob("cyg*.dll")]:
    out = subprocess.check_output(["objdump", "-p", str(path)], text=True, errors="replace")
    for line in out.splitlines():
        m = re.match(r"\tDLL Name: (\S+)", line)
        if not m:
            continue
        name = m.group(1)
        if name.upper() in {"KERNEL32.DLL", "NTDLL.DLL"}:
            continue
        if name.lower().startswith("api-ms-"):
            continue
        if name.lower().startswith("cyg") and not (tools / name.lower()).exists():
            need.add(name)
print("\n".join(sorted(need)))
PY
  )"
  [[ -z "${missing}" ]]
}

need_fetch=0
if ! _verify_mke2fs_deps; then
  need_fetch=1
fi

if [[ "${need_fetch}" -eq 0 ]]; then
  echo "OK: mke2fs + DLL (32 bit) in ${TOOLS}"
  ls -la "${TOOLS}"
else
  echo "Scarico runtime Cygwin x86 (32 bit) per mke2fs..."
  TMP="$(mktemp -d)"
  trap 'rm -rf "${TMP}"' EXIT

  for pkg in "${CYG_PACKAGES[@]}"; do
    name="$(basename "${pkg}")"
    archive="${TMP}/${name}"
    echo "  ${pkg}"
    curl -fsSL -o "${archive}" "${CYG_BASE}/${pkg}"
    tar -xf "${archive}" -C "${TMP}"
  done

  MKE2FS="$(find "${TMP}" \( -path '*/usr/sbin/mke2fs.exe' -o -path '*/usr/bin/mke2fs.exe' \) -type f | head -1)"
  if [[ -z "${MKE2FS}" ]]; then
    echo "ERRORE: mke2fs.exe non trovato negli archivi Cygwin." >&2
    exit 1
  fi

  rm -f "${TOOLS}"/cyg*.dll "${TOOLS}"/mke2fs.exe
  cp -f "${MKE2FS}" "${TOOLS}/"

  while IFS= read -r -d '' dll; do
    cp -f "${dll}" "${TOOLS}/"
  done < <(find "${TMP}" -path '*/usr/bin/cyg*.dll' -type f -print0)

  if ! _verify_mke2fs_deps; then
    echo "ERRORE: dipendenze mke2fs incomplete in ${TOOLS}" >&2
    ls -la "${TOOLS}" >&2
    exit 1
  fi

  if file -b "${TOOLS}/mke2fs.exe" | grep -q 'PE32+'; then
    echo "ERRORE: mke2fs non e' 32 bit." >&2
    exit 1
  fi

  echo "Strumenti e2fsprogs (32 bit) in ${TOOLS}:"
  ls -la "${TOOLS}"
  rm -rf "${TMP}"
  trap - EXIT
fi

# GPT fdisk (sgdisk32) — append partizioni dopo dd ISO (equivalente sfdisk --append)
GPTFDISK_DIR="${SCRIPT_DIR}/tools/gptfdisk"
GPTFDISK_ZIP_URL="https://downloads.sourceforge.net/project/gptfdisk/gptfdisk/1.0.10/gdisk-binaries/gdisk-windows-1.0.10.zip"

mkdir -p "${GPTFDISK_DIR}"

if [[ -f "${GPTFDISK_DIR}/sgdisk32.exe" ]]; then
  echo "OK: ${GPTFDISK_DIR}/sgdisk32.exe"
else
  echo "Scarico gptfdisk Windows (sgdisk32, 32 bit — funziona anche su Win64)..."
  GPT_TMP="$(mktemp -d)"
  trap 'rm -rf "${GPT_TMP}"' EXIT
  curl -fsSL -o "${GPT_TMP}/gdisk-windows.zip" "${GPTFDISK_ZIP_URL}"
  unzip -q -j "${GPT_TMP}/gdisk-windows.zip" "sgdisk32.exe" -d "${GPTFDISK_DIR}" 2>/dev/null \
    || unzip -q -j "${GPT_TMP}/gdisk-windows.zip" "*/sgdisk32.exe" -d "${GPTFDISK_DIR}"
  if [[ ! -f "${GPTFDISK_DIR}/sgdisk32.exe" ]]; then
    echo "ERRORE: sgdisk32.exe non trovato nello zip gptfdisk." >&2
    unzip -l "${GPT_TMP}/gdisk-windows.zip" | head -20 >&2
    exit 1
  fi
  chmod +x "${GPTFDISK_DIR}/sgdisk32.exe"
  rm -f "${GPTFDISK_DIR}/sgdisk64.exe"
  if file -b "${GPTFDISK_DIR}/sgdisk32.exe" | grep -q 'PE32+'; then
    echo "ERRORE: sgdisk32.exe non e' 32 bit." >&2
    exit 1
  fi
  if ! file -b "${GPTFDISK_DIR}/sgdisk32.exe" | grep -q 'PE32'; then
    echo "ERRORE: sgdisk32.exe non riconosciuto come PE32." >&2
    exit 1
  fi
  echo "sgdisk32 in ${GPTFDISK_DIR}:"
  ls -la "${GPTFDISK_DIR}/sgdisk32.exe"
  rm -rf "${GPT_TMP}"
  trap - EXIT
fi

# Pre-installa Python nella cartella windows/python (evita installer su WinBoat)
PYDIR="${SCRIPT_DIR}/python"
if [[ -f "${PYDIR}/python.exe" ]] && [[ -f "${PYDIR}/tcl/tcl8.6/init.tcl" ]] \
  && grep -qx 'DLLs' "${PYDIR}/python39._pth" 2>/dev/null; then
  echo "OK: ${PYDIR}/python.exe (pre-installato, tkinter)"
else
  chmod +x "${SCRIPT_DIR}/unpack-python-win32.sh"
  "${SCRIPT_DIR}/unpack-python-win32.sh" || echo "NOTA: unpack Python fallito; installer .exe su Windows al primo avvio."
fi

# FATtools — formattazione exFAT in-place su PhysicalDrive @ offset (no volumi Windows)
FATTOOLS_DIR="${SCRIPT_DIR}/python/Lib/FATtools"
FATTOOLS_REPO="https://github.com/maxpat78/FATtools.git"

if [[ -f "${FATTOOLS_DIR}/mkfat.py" ]] && [[ -f "${FATTOOLS_DIR}/disk.py" ]]; then
  echo "OK: ${FATTOOLS_DIR}"
else
  echo "Scarico FATtools (exFAT in-place su PhysicalDrive)..."
  mkdir -p "${SCRIPT_DIR}/python/Lib"
  FAT_TMP="$(mktemp -d)"
  trap 'rm -rf "${FAT_TMP}"' EXIT
  git clone --depth 1 "${FATTOOLS_REPO}" "${FAT_TMP}/FATtools-src"
  rm -rf "${FATTOOLS_DIR}"
  cp -a "${FAT_TMP}/FATtools-src/FATtools" "${FATTOOLS_DIR}"
  rm -rf "${FAT_TMP}"
  trap - EXIT
  if [[ ! -f "${FATTOOLS_DIR}/mkfat.py" ]]; then
    echo "ERRORE: FATtools incompleto in ${FATTOOLS_DIR}" >&2
    exit 1
  fi
  echo "FATtools in ${FATTOOLS_DIR}:"
  ls -la "${FATTOOLS_DIR}/mkfat.py" "${FATTOOLS_DIR}/disk.py"
fi

echo ""
echo "=== Verifica stack 32 bit (PE32, no PE32+) ==="
_pe32_fail=0
while IFS= read -r -d '' bin; do
  kind="$(file -b "${bin}")"
  if echo "${kind}" | grep -q 'PE32+'; then
    echo "ERRORE 64 bit: ${bin}" >&2
    _pe32_fail=1
  elif echo "${kind}" | grep -Eq 'PE32 executable|PE32 executable \(DLL\)|PE32\+ \(force\)'; then
    : # ok
  elif echo "${kind}" | grep -q 'PE32'; then
    : # ok (DLL, etc.)
  else
    echo "  skip: ${bin} (${kind})"
  fi
done < <(find "${SCRIPT_DIR}/tools" "${SCRIPT_DIR}/python" \( -name '*.exe' -o -name '*.dll' -o -name '*.pyd' \) -print0 2>/dev/null)

if [[ "${_pe32_fail}" -ne 0 ]]; then
  echo "ERRORE: trovati binari 64 bit nello stack Windows." >&2
  exit 1
fi
echo "OK: stack Windows 32 bit (python + mke2fs + sgdisk32 + FATtools)"
