#!/bin/bash
# Carica lo store salvato con salva_sessione.
#
# Uso:
#   load_sessione                    # terminale, domanda per ogni voce (7 + admin)
#   load_sessione --gui --only rete  # chiamata dalla GUI (senza prompt)
#   load_sessione --only network,display
#
# ID voce utente (--only): network cups bluetooth config audio display firefox
set -o pipefail

QUELO_SESSION_VERSION="v10"
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SESSION_NAME="load_sessione"
LOAD_GUI=0
LOAD_KEEP_MOUNT=0
LOAD_INTERACTIVE=1
LOAD_NO_FINALIZE=0
LOAD_FINALIZE_ONLY=0
declare -a LOAD_STEPS=()

declare -a SUMMARY_OK=()
declare -a SUMMARY_SKIP=()
declare -a SUMMARY_ERR=()

. "${_SCRIPT_DIR}/quelo-session-common.sh"

usage() {
  cat <<'EOF'
Uso: load_sessione [opzioni]

  --gui              modalita' GUI (log su file, persistence resta montata)
  --only ID[,ID...]  ripristina solo le voci indicate (senza prompt)
  --no-finalize      ripristina senza sync finale (uso GUI, step singolo)
  --finalize-only    solo sync finale
  --help             questo messaggio

ID: network cups bluetooth config audio display firefox
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gui)
        LOAD_GUI=1
        LOAD_KEEP_MOUNT=1
        LOAD_INTERACTIVE=0
        shift
        ;;
      --only)
        LOAD_INTERACTIVE=0
        IFS=',' read -ra LOAD_STEPS <<< "${2:-}"
        shift 2
        ;;
      --no-finalize)
        LOAD_NO_FINALIZE=1
        shift
        ;;
      --finalize-only)
        LOAD_FINALIZE_ONLY=1
        LOAD_GUI=1
        LOAD_KEEP_MOUNT=1
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "Opzione sconosciuta: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

step_enabled() {
  local id="$1" s
  for s in "${LOAD_STEPS[@]}"; do
    [[ "${s}" == "${id}" ]] && return 0
  done
  return 1
}

verbose() {
  session_log "$*"
}

step_ok() { verbose "  [OK] $*"; SUMMARY_OK+=("$*"); }
step_skip() { verbose "  [SALTO] $*"; SUMMARY_SKIP+=("$*"); }
step_err() { verbose "  [ERRORE] $*"; SUMMARY_ERR+=("$*"); }

ask_yes() {
  local prompt="$1" ans
  session_debug "PROMPT: ${prompt}"
  read -r -p "${prompt} [s/N]: " ans
  session_debug "RISPOSTA: ${ans:-<vuoto>}"
  [[ "${ans,,}" == s || "${ans,,}" == si ]]
}

restore_mirror() {
  local label="$1" src="$2" dst="$3"
  verbose "  Ripristino: ${src} -> ${dst}"
  if session_mirror_dir "${label}" "${src}" "${dst}"; then
    step_ok "${label}"
    return 0
  fi
  step_skip "${label} (assente in store o errore)"
  return 1
}

restore_file() {
  local label="$1" src="$2" dst="$3"
  verbose "  Ripristino file: ${src} -> ${dst}"
  if session_copy_file "${label}" "${src}" "${dst}"; then
    step_ok "${label}"
    return 0
  fi
  step_skip "${label}"
  return 1
}

load_firefox() {
  session_debug "=== LOAD firefox ==="
  local src="${STORE}/firefox"
  local archive="${src}/mozilla-backup.tar.gz"
  session_debug_path "store-firefox" "${src}" 3

  if [[ -f "${archive}" ]]; then
    if session_restore_mozilla_home "${archive}"; then
      step_ok "~/.mozilla ripristinato (preferiti, cronologia, login, impostazioni)"
      verbose "   Riavvia Firefox."
      return 0
    fi
    step_err "firefox (estrazione tar fallita)"
    return 1
  fi

  step_skip "firefox (mozilla-backup.tar.gz assente in store — salva di nuovo)"
  return 2
}

load_display() {
  session_debug "=== LOAD display ==="
  local src="${STORE}/display" applied=0
  session_debug_path "store-display" "${src}" 5
  session_ensure_x11

  if [[ -x "${src}/xrandr-restore.sh" ]]; then
    if session_xrandr >/dev/null; then
      verbose "  Eseguo xrandr-restore.sh..."
      session_debug_cmd "xrandr-before" session_xrandr_timeout --current --nograb || true
      session_debug_cmd "run-restore" bash -x "${src}/xrandr-restore.sh" || true
      sleep 1
      session_debug_cmd "xrandr-after" session_xrandr_timeout --current --nograb || true
      step_ok "xrandr applicato"
      applied=1
    else
      step_skip "xrandr (server X non raggiungibile su ${DISPLAY})"
    fi
  else
    step_skip "xrandr-restore.sh (assente o non eseguibile)"
  fi

  restore_mirror "lxrandr" "${src}/lxrandr" /root/.config/lxrandr && applied=1
  if [[ -f "${src}/autostart/lxrandr-autostart.desktop" ]]; then
    session_debug_cmd "mkdir-autostart" mkdir -p /root/.config/autostart
    session_copy_file "lxrandr-autostart" \
      "${src}/autostart/lxrandr-autostart.desktop" \
      /root/.config/autostart/lxrandr-autostart.desktop && applied=1
  fi
  [[ "${applied}" -eq 0 ]] && step_skip "display (nulla applicato)"
}

load_audio() {
  session_debug "=== LOAD audio ==="
  local src="${STORE}/audio" reloaded=0
  session_debug_path "store-audio" "${src}" 5

  restore_mirror "pulse" "${src}/pulse" /root/.config/pulse || true

  if [[ -f "${src}/asound.state" ]] && command -v alsactl >/dev/null 2>&1; then
    session_debug_cmd "alsactl-restore" alsactl -f "${src}/asound.state" restore && step_ok "alsactl restore" || step_err "alsactl restore"
  else
    step_skip "asound.state"
  fi

  if command -v pulseaudio >/dev/null 2>&1; then
    verbose "  Riavvio PulseAudio..."
    session_debug_cmd "pulseaudio-kill" pulseaudio -k || true
    sleep 1
    session_debug_cmd "pulseaudio-start" pulseaudio --start || true
    session_debug_cmd "pactl-info-after" pactl info || true
    reloaded=1
  fi
  if [[ "${reloaded}" -eq 1 ]]; then
    session_debug_cmd "quelo-audio-default" /usr/local/bin/quelo-audio-default.sh --pulse || true
  fi
}

restart_lxqt_panel() {
  session_debug_cmd "pkill-panel" pkill -x lxqt-panel || true
  sleep 1
  session_ensure_x11
  session_debug ">>> CMD [start-panel] (background): lxqt-panel"
  lxqt-panel &
  local pid=$!
  session_debug "<<< SPAWN [start-panel] pid=${pid}"
  sleep 1
  if pgrep -x lxqt-panel >/dev/null 2>&1; then
    step_ok "lxqt-panel riavviato (pid ${pid})"
    return 0
  fi
  step_err "lxqt-panel non riavviato"
  return 1
}

load_config() {
  session_debug "=== LOAD config ==="
  if restore_mirror "config" "${STORE}/config" /root/.config; then
    verbose "   ~/.config ripristinata."
    if [[ -x /usr/local/bin/quelo-font-apply-at-start ]]; then
      /usr/local/bin/quelo-font-apply-at-start || true
    fi
    session_debug_cmd "openbox-reconfigure" openbox --reconfigure || true
    if [[ "${LOAD_INTERACTIVE}" -eq 1 ]]; then
      if ask_yes "   Riavviare lxqt-panel adesso?"; then
        restart_lxqt_panel || true
      fi
    else
      restart_lxqt_panel || true
    fi
  fi
}

load_bluetooth() {
  session_debug "=== LOAD bluetooth ==="
  if restore_mirror "bluetooth" "${STORE}/bluetooth" /var/lib/bluetooth; then
    verbose "  Riavvio Bluetooth..."
    session_debug_cmd "systemctl-bluetooth" systemctl restart bluetooth.service || true
    session_debug_cmd "systemctl-bluetooth-status" systemctl status bluetooth.service --no-pager || true
  fi
}

load_network() {
  session_debug "=== LOAD rete ==="
  local src="${STORE}/etc" ok=0
  restore_mirror "NM-connections" "${src}/NetworkManager/system-connections" \
    /etc/NetworkManager/system-connections && ok=1
  restore_mirror "NM-conf.d" "${src}/NetworkManager/conf.d" \
    /etc/NetworkManager/conf.d && ok=1
  if [[ "${ok}" -eq 1 ]]; then
    session_debug_cmd "chmod-nm" chmod 700 /etc/NetworkManager/system-connections || true
    session_debug_cmd "chmod-nm-files" chmod 600 /etc/NetworkManager/system-connections/* 2>/dev/null || true
    verbose "  Riavvio NetworkManager..."
    session_debug_cmd "systemctl-nm-restart" systemctl restart NetworkManager.service || true
    session_debug_cmd "systemctl-nm-status" systemctl status NetworkManager.service --no-pager || true
    step_ok "NetworkManager"
  else
    step_skip "rete"
  fi
}

load_cups() {
  session_debug "=== LOAD cups ==="
  if restore_mirror "cups" "${STORE}/etc/cups" /etc/cups; then
    verbose "  Riavvio CUPS..."
    session_debug_cmd "systemctl-cups" systemctl restart cups.service || true
    session_debug_cmd "systemctl-cups-status" systemctl status cups.service --no-pager || true
  fi
}

load_apt_sources() {
  session_debug "=== LOAD apt sources ==="
  restore_mirror "apt-sources" "${STORE}/etc/apt/sources.list.d" /etc/apt/sources.list.d
}

load_hostname() {
  session_debug "=== LOAD hostname ==="
  if restore_file "hostname" "${STORE}/etc/hostname" /etc/hostname; then
    session_debug_cmd "hostname-set" hostname "$(cat /etc/hostname)" || true
  fi
}

load_machine_id() {
  session_debug "=== LOAD machine-id ==="
  restore_file "machine-id" "${STORE}/etc/machine-id" /etc/machine-id
}

print_summary() {
  local i
  [[ "${LOAD_GUI}" -eq 1 ]] && return 0
  echo ""
  echo "============================================================"
  echo " RIEPILOGO CARICAMENTO"
  echo "============================================================"
  ((${#SUMMARY_OK[@]})) && { echo "OK (${#SUMMARY_OK[@]}):"; for i in "${!SUMMARY_OK[@]}"; do printf "  [OK] %s\n" "${SUMMARY_OK[$i]}"; done; echo ""; }
  ((${#SUMMARY_SKIP[@]})) && { echo "SALTATI (${#SUMMARY_SKIP[@]}):"; for i in "${!SUMMARY_SKIP[@]}"; do printf "  [--] %s\n" "${SUMMARY_SKIP[$i]}"; done; echo ""; }
  ((${#SUMMARY_ERR[@]})) && { echo "ERRORI (${#SUMMARY_ERR[@]}):"; for i in "${!SUMMARY_ERR[@]}"; do printf "  [!!] %s\n" "${SUMMARY_ERR[$i]}"; done; echo ""; }
  echo "Log:   ${LOGFILE}"
  echo "Debug: ${DEBUGLOG}"
  ((${#SUMMARY_ERR[@]})) && echo "CARICAMENTO CON ERRORI." || echo "CARICAMENTO COMPLETATO."
  echo "============================================================"
}

run_step() {
  local n="$1" title="$2"
  shift 2
  echo ""
  echo "------------------------------------------------------------"
  echo " ${n} — ${title}"
  echo "------------------------------------------------------------"
  session_debug ">>> STEP ${n}: ${title}"
  if ask_yes "${n} — ${title}"; then
    "$@" || true
  else
    step_skip "${title} (scelta utente: no)"
  fi
}

run_batch_step() {
  local id="$1" title="$2" rc=0
  shift 2
  session_debug ">>> STEP [${id}]: ${title}"
  if step_enabled "${id}"; then
    "$@" || rc=$?
    return "${rc}"
  fi
  session_debug "SKIP batch: ${id} (non selezionato)"
  return 2
}

load_step_exit_code() {
  if ((${#SUMMARY_ERR[@]} > 0)); then
    return 1
  fi
  if ((${#SUMMARY_OK[@]} > 0)); then
    return 0
  fi
  return 2
}

load_finalize() {
  session_debug_cmd "sync" sync
}

session_umount_on_exit() {
  sync 2>/dev/null || true
  session_debug "sync completato (trap EXIT)"
  session_debug_store_tree || true
  session_debug "========== FINE DEBUG SISTEMA =========="
  if [[ "${LOAD_KEEP_MOUNT}" -eq 1 ]]; then
    session_debug "LOAD_KEEP_MOUNT=1: persistence resta montata"
    return 0
  fi
  if mountpoint -q "${PERSIST_MNT}" 2>/dev/null; then
    session_debug_cmd "umount" umount "${PERSIST_MNT}" || session_debug_cmd "umount-lazy" umount -l "${PERSIST_MNT}" || true
  fi
}

# --- main ---

parse_args "$@"

if [[ "${LOAD_GUI}" -eq 0 ]]; then
  echo ""
  echo "Quelo Office — load_sessione ${QUELO_SESSION_VERSION}"
  echo "======================================================="
fi

session_debug "VERSIONE SCRIPT: ${QUELO_SESSION_VERSION} dir=${_SCRIPT_DIR} gui=${LOAD_GUI}"
session_debug_header

if ! session_mount_persistence; then
  echo "ERRORE: impossibile montare '${PERSIST_LABEL}'." >&2
  exit 1
fi

trap session_umount_on_exit EXIT

if [[ ! -d "${STORE}" ]]; then
  echo "ERRORE: store assente (${STORE}). Esegui prima salva_sessione." >&2
  session_debug "FATAL: store assente"
  exit 1
fi

session_debug_store_tree

if [[ "${LOAD_GUI}" -eq 0 ]]; then
  [[ -f "${STORE}/.quelo-last-save" ]] && echo "Ultimo salvataggio: $(cat "${STORE}/.quelo-last-save")"
  echo "Store: ${STORE}"
  echo "Debug: ${DEBUGLOG}"
  echo ""
  if [[ ${#LOAD_STEPS[@]} -eq 0 ]]; then
    echo "Per ogni voce: s/si = ripristina, Invio/n = salta."
    echo ""
  fi
fi

session_log "=== INIZIO caricamento sessione (gui=${LOAD_GUI}) ==="

if [[ "${LOAD_FINALIZE_ONLY}" -eq 1 ]]; then
  load_finalize
  session_log "=== FINE caricamento sessione (finalize-only) ==="
  exit 0
fi

if [[ ${#LOAD_STEPS[@]} -gt 0 ]]; then
  run_batch_step network "Rete (NetworkManager)" load_network || true
  run_batch_step cups "Stampanti (CUPS)" load_cups || true
  run_batch_step bluetooth "Bluetooth" load_bluetooth || true
  run_batch_step config "Config (~/.config)" load_config || true
  run_batch_step audio "Audio" load_audio || true
  run_batch_step display "Schermo (xrandr)" load_display || true
  run_batch_step firefox "Firefox" load_firefox || true
else
  run_step "1/10" "Hostname" load_hostname
  run_step "2/10" "Machine-ID" load_machine_id
  run_step "3/10" "Rete (NetworkManager)" load_network
  run_step "4/10" "Stampanti (CUPS)" load_cups
  run_step "5/10" "Bluetooth" load_bluetooth
  run_step "6/10" "Config (~/.config)" load_config
  run_step "7/10" "Audio" load_audio
  run_step "8/10" "Schermo (xrandr)" load_display
  run_step "9/10" "Firefox" load_firefox
  run_step "10/10" "Sorgenti apt" load_apt_sources
fi

if [[ "${LOAD_NO_FINALIZE}" -eq 1 ]]; then
  session_log "=== FINE step singolo (no-finalize) ==="
  load_step_exit_code
  exit $?
fi

load_finalize
session_log "=== FINE caricamento sessione ==="
print_summary

load_step_exit_code
exit $?
