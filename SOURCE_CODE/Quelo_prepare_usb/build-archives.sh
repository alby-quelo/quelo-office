#!/bin/bash
# Rigenera zip / rar / tar dei pacchetti prepare-usb in DOWNLOAD/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VERSION_FILE="${PROJECT_DIR}/SOURCE_CODE/Quelo_office/VERSION"
OUT_DIR="${PROJECT_DIR}/DOWNLOAD"
STAGE="${OUT_DIR}/.staging"

VER="$(grep '^QUELO_PUBLISH_ISO_VERSION=' "${SCRIPT_DIR}/prepare-usb.sh" | sed 's/.*"\(.*\)".*/\1/')-alpha"
if [[ -z "${VER}" || "${VER}" == "-alpha" ]]; then
  if [[ -f "${VERSION_FILE}" ]]; then
    VER="$(tr -d '[:space:]' <"${VERSION_FILE}")-alpha"
  else
    VER="0.71-alpha"
  fi
fi

PKG="Quelo_prepare_usb"
PKG_GUI="Quelo_prepare_usb_gui"
BASE="${OUT_DIR}/${PKG}-${VER}"
BASE_GUI="${OUT_DIR}/${PKG_GUI}-${VER}"

FULL_FILES=(
  prepare-usb.sh
  prepare-usb-gui.sh
  prepare-usb-gui.py
  quelo_prepare_lib.py
  quelo-write-iso.py
  logo.png
  NOTES-MANUALE.txt
  build-archives.sh
)

GUI_FILES=(
  prepare-usb-gui.sh
  prepare-usb-gui.py
  quelo_prepare_lib.py
  quelo-write-iso.py
  logo.png
  NOTES-MANUALE.txt
)

mkdir -p "${OUT_DIR}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}/${PKG}" "${STAGE}/${PKG_GUI}"

for name in "${FULL_FILES[@]}"; do
  cp "${SCRIPT_DIR}/${name}" "${STAGE}/${PKG}/"
done
chmod +x "${STAGE}/${PKG}/prepare-usb.sh" "${STAGE}/${PKG}/prepare-usb-gui.sh" "${STAGE}/${PKG}/build-archives.sh"

for name in "${GUI_FILES[@]}"; do
  cp "${SCRIPT_DIR}/${name}" "${STAGE}/${PKG_GUI}/"
done
chmod +x "${STAGE}/${PKG_GUI}/prepare-usb-gui.sh"

rm -f "${BASE}.zip" "${BASE}.rar" "${BASE}.tar"
rm -f "${BASE_GUI}.zip" "${BASE_GUI}.rar" "${BASE_GUI}.tar"

cd "${STAGE}"
zip -r "${BASE}.zip" "${PKG}/"
rar a -r "${BASE}.rar" "${PKG}/"
tar -cf "${BASE}.tar" "${PKG}/"

zip -r "${BASE_GUI}.zip" "${PKG_GUI}/"
rar a -r "${BASE_GUI}.rar" "${PKG_GUI}/"
tar -cf "${BASE_GUI}.tar" "${PKG_GUI}/"

rm -rf "${STAGE}"

echo "Creati:"
ls -lah "${BASE}".{zip,rar,tar} "${BASE_GUI}".{zip,rar,tar}
