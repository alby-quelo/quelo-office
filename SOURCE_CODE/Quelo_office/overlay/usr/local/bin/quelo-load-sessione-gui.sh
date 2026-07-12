#!/bin/bash
# Dialogo ripristino all'avvio + progresso + riepilogo (sempre visibile).
set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="load_sessione-gui"
# shellcheck source=/dev/null
. "${_SCRIPT_DIR}/quelo-session-common.sh"
# shellcheck source=/dev/null
. "${_SCRIPT_DIR}/quelo-session-gui-lib.sh"

session_ensure_x11

quelo_gui_log() {
  logger -t quelo-load-sessione-gui "$*" 2>/dev/null || true
}

if ! command -v zenity >/dev/null 2>&1; then
  quelo_gui_log "zenity assente"
  exit 0
fi

persist_ok=1
if ! quelo_session_gui_wait_persist; then
  quelo_gui_log "persistence non montata (mostro comunque la checklist)"
  persist_ok=0
else
  mkdir -p "${STORE}"
fi

intro="Deseleziona le voci che NON vuoi ripristinare.
Se hai cambiato monitor, togli la spunta da «Schermo e risoluzione»."

if [[ "${persist_ok}" -eq 0 ]]; then
  intro="ATTENZIONE: partizione persistence non montata.
Il ripristino non potra' completarsi finche' non e' disponibile.

${intro}"
fi

if [[ -f "${STORE}/.quelo-last-save" ]]; then
  intro="Ultimo salvataggio: $(cat "${STORE}/.quelo-last-save")

${intro}"
else
  intro="Primo avvio (nessun salvataggio precedente).
Premi «No, grazie» per continuare senza ripristino.

${intro}"
fi

quelo_session_gui_standard_checklist

# shellcheck disable=SC2068
selected="$(
  zenity --list --checklist \
    --title="Ripristinare i settaggi salvati?" \
    --text="${intro}" \
    --width=520 --height=440 \
    --ok-label="OK" \
    --cancel-label="No, grazie" \
    --column="" --column="ID" --column="Voce" \
    "${QUELO_GUI_CHECKLIST[@]}" 2>/dev/null
)" || {
  quelo_gui_log "No, grazie (checklist)"
  exit 0
}

if [[ -z "${selected}" ]]; then
  quelo_gui_log "OK senza selezioni"
  exit 0
fi

declare -a LOAD_IDS=()
quelo_session_gui_parse_selected "${selected}" LOAD_IDS
quelo_gui_log "ripristino: ${LOAD_IDS[*]}"

quelo_session_gui_run_steps "${_SCRIPT_DIR}/load_sessione.sh" load "${LOAD_IDS[@]}"
quelo_session_gui_show_results "Ripristino completato" "Fatto!"
exit 0
