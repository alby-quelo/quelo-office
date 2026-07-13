#!/bin/bash
# Avvia la GUI prepare-usb sul PC HOST (pkexec/sudo se necessario).
# NON per la live Quelo Office.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUI="${SCRIPT_DIR}/prepare-usb-gui.py"

if grep -q ' boot=live' /proc/cmdline 2>/dev/null; then
  echo "ERRORE: sei in sessione LIVE. Esegui dal PC host, non dalla USB avviata." >&2
  exit 1
fi

if [[ ! -f "${GUI}" ]]; then
  echo "ERRORE: prepare-usb-gui.py non trovato." >&2
  exit 1
fi

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-${HOME}/.Xauthority}"

if [[ "${EUID}" -ne 0 ]]; then
  if command -v pkexec >/dev/null 2>&1; then
    exec pkexec env DISPLAY="${DISPLAY}" XAUTHORITY="${XAUTHORITY}" \
      python3 "${GUI}" "$@"
  fi
  exec sudo env DISPLAY="${DISPLAY}" XAUTHORITY="${XAUTHORITY}" \
    python3 "${GUI}" "$@"
fi

exec python3 "${GUI}" "$@"
