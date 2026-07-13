#!/bin/bash
# Rigenera zip / rar / tar del pacchetto prepare-usb in DOWNLOAD/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VERSION_FILE="${PROJECT_DIR}/SOURCE_CODE/Quelo_office/VERSION"
OUT_DIR="${PROJECT_DIR}/DOWNLOAD"

VER="$(grep '^QUELO_PUBLISH_ISO_VERSION=' "${SCRIPT_DIR}/prepare-usb.sh" | sed 's/.*"\(.*\)".*/\1/')-alpha"
if [[ -z "${VER}" || "${VER}" == "-alpha" ]]; then
  if [[ -f "${VERSION_FILE}" ]]; then
    VER="$(tr -d '[:space:]' <"${VERSION_FILE}")-alpha"
  else
    VER="0.71-alpha"
  fi
fi

PKG="Quelo_prepare_usb"
BASE="${OUT_DIR}/${PKG}-${VER}"

mkdir -p "${OUT_DIR}"
cd "$(dirname "${SOURCE_DIR}")"

rm -f "${BASE}.zip" "${BASE}.rar" "${BASE}.tar"

zip -r "${BASE}.zip" "$(basename "${SOURCE_DIR}")/"
rar a -r "${BASE}.rar" "$(basename "${SOURCE_DIR}")/"
tar -cf "${BASE}.tar" "$(basename "${SOURCE_DIR}")/"

echo "Creati:"
ls -lah "${BASE}".{zip,rar,tar}
