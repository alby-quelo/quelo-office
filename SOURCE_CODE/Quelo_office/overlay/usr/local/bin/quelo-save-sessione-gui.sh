#!/bin/bash
# Dialogo salvataggio selettivo + progresso + riepilogo.
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="salva_sessione-gui"
POWER_ACTION="${1:-}"

# shellcheck source=/dev/null
. "${_SCRIPT_DIR}/quelo-session-common.sh"
# shellcheck source=/dev/null
. "${_SCRIPT_DIR}/quelo-session-gui-lib.sh"

session_ensure_x11

quelo_gui_log() {
  logger -t quelo-save-sessione-gui "$*" 2>/dev/null || true
}

if ! command -v zenity >/dev/null 2>&1; then
  quelo_gui_log "zenity assente"
  exit 0
fi

if ! quelo_session_gui_wait_persist; then
  zenity --warning \
    --title="Salva sessione" \
    --text="Partizione persistence non trovata.\nImpossibile salvare." \
    --width=420 2>/dev/null || true
  exit 1
fi

intro="Deseleziona le voci che NON vuoi salvare."

quelo_session_gui_standard_checklist

# shellcheck disable=SC2068
selected="$(
  zenity --list --checklist \
    --title="Salvare le configurazioni della sessione?" \
    --text="${intro}" \
    --width=520 --height=440 \
    --ok-label="OK" \
    --cancel-label="No, grazie" \
    --column="" --column="ID" --column="Voce" \
    "${QUELO_GUI_CHECKLIST[@]}" 2>/dev/null
)" || {
  quelo_gui_log "No, grazie (checklist)"
  if [[ -n "${POWER_ACTION}" ]]; then
    quelo_session_gui_confirm_power_no_save "${POWER_ACTION}"
    exit $?
  fi
  exit 0
}

if [[ -z "${selected}" ]]; then
  quelo_gui_log "OK senza selezioni"
  if [[ -n "${POWER_ACTION}" ]]; then
    quelo_session_gui_confirm_power_no_save "${POWER_ACTION}"
    exit $?
  fi
  exit 0
fi

declare -a SAVE_IDS=()
quelo_session_gui_parse_selected "${selected}" SAVE_IDS
quelo_gui_log "salvataggio: ${SAVE_IDS[*]}"

quelo_session_gui_run_steps "${_SCRIPT_DIR}/salva_sessione.sh" save "${SAVE_IDS[@]}"

if [[ -n "${POWER_ACTION}" ]]; then
  quelo_session_gui_show_results \
    "Salvataggio completato" \
    "$(quelo_session_gui_power_label "${POWER_ACTION}")" \
    "Torna indietro"
  exit $?
fi

quelo_session_gui_show_results "Salvataggio completato" "Fatto!"
exit 0
