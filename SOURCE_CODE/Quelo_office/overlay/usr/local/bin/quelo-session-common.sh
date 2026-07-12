#!/bin/bash
# Libreria condivisa salva_sessione / load_sessione — log + DEBUG completo.
# Ogni comando, ogni path, ogni exit code finisce nel log debug su persistenza.
[[ -n "${QUELO_SESSION_COMMON_LOADED:-}" ]] && return 0
QUELO_SESSION_COMMON_LOADED=1

PERSIST_LABEL="${PERSIST_LABEL:-persistence}"
PERSIST_MNT="${PERSIST_MNT:-/media/quelo-persist}"
STORE="${STORE:-${PERSIST_MNT}/store}"
LOGFILE="${LOGFILE:-${PERSIST_MNT}/quelo-persist.log}"
DEBUGLOG="${DEBUGLOG:-${PERSIST_MNT}/quelo-session-debug.log}"
SESSION_TMP_DEBUG="/tmp/quelo-session-debug.$$"

session_debug_write() {
  local line="$1"
  echo "${line}" >>"${SESSION_TMP_DEBUG}" 2>/dev/null || true
  if [[ -n "${DEBUGLOG_READY:-}" && -w "$(dirname "${DEBUGLOG}")" ]]; then
    echo "${line}" >>"${DEBUGLOG}" 2>/dev/null || true
  fi
}

session_debug() {
  local ts line
  ts="$(date '+%Y-%m-%d %H:%M:%S.%N' 2>/dev/null || date '+%Y-%m-%d %H:%M:%S')"
  line="[DEBUG ${ts}] [${SESSION_NAME:-session}] $*"
  echo "${line}"
  session_debug_write "${line}"
}

session_log() {
  local ts msg
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  msg="[${ts}] ${SESSION_NAME:-session}: $*"
  echo "${msg}"
  session_debug_write "${msg}"
  if [[ -n "${DEBUGLOG_READY:-}" ]]; then
    echo "${msg}" >>"${LOGFILE}" 2>/dev/null || true
  fi
}

session_debug_flush_tmp() {
  if [[ -f "${SESSION_TMP_DEBUG}" && -n "${DEBUGLOG_READY:-}" ]]; then
    {
      echo ""
      echo "========== flush debug temporaneo $(date -Iseconds) =========="
      cat "${SESSION_TMP_DEBUG}"
    } >>"${DEBUGLOG}" 2>/dev/null || true
    rm -f "${SESSION_TMP_DEBUG}"
  fi
}

session_debug_enable_on_persist() {
  DEBUGLOG_READY=1
  mkdir -p "${PERSIST_MNT}" "${STORE}" 2>/dev/null || true
  {
    echo ""
    echo "################################################################"
    echo "# ${SESSION_NAME:-session} — $(date -Iseconds)"
    echo "################################################################"
  } >>"${DEBUGLOG}" 2>/dev/null || true
  session_debug_flush_tmp
}

session_debug_header() {
  session_debug "========== INIZIO DEBUG SISTEMA =========="
  session_debug "script=${SESSION_NAME:-?} pid=$$ ppid=${PPID:-?} uid=$(id -u) user=$(id -un)"
  session_debug "pwd=$(pwd) shell=${BASH_VERSION:-?}"
  session_debug "DISPLAY=${DISPLAY:-<unset>} XAUTHORITY=${XAUTHORITY:-<unset>}"
  session_debug "PATH=${PATH:-<unset>}"
  session_debug_cmd "date" date -Iseconds
  session_debug_cmd "uname" uname -a
  session_debug_cmd "blkid-persistence" blkid -L "${PERSIST_LABEL}" || true
  session_debug_cmd "lsblk" lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
  session_debug_cmd "mount-grep-persist" sh -c "mount | grep -F persist || true"
  session_debug_cmd "df-persist" sh -c "df -h '${PERSIST_MNT}' 2>/dev/null || df -h | grep -F persist || true"
}

session_ensure_x11() {
  export DISPLAY="${DISPLAY:-:0}"
  export XAUTHORITY="${XAUTHORITY:-/root/.Xauthority}"
}

# Esegue xrandr con timeout FORZATO: -k 3 8 = manda SIGTERM dopo 8s e, se
# xrandr lo ignora (tipico quando resta bloccato su un grab del server X,
# vedi manuale: "xrandr just hangs, no error"), SIGKILL dopo altri 3s.
# Cosi' non resta MAI appeso oltre ~11s.
session_xrandr_timeout() {
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 3 8 xrandr "$@"
  else
    xrandr "$@"
  fi
}

# Scrive l'output di xrandr --current --nograb su file (no subshell).
# Ritorna 0 se il file ha contenuto utile, 1 altrimenti.
session_xrandr_to_file() {
  local out_file="$1" err_file="${2:-/dev/null}"
  session_ensure_x11
  local rc=0
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 3 8 xrandr --current --nograb >"${out_file}" 2>"${err_file}"
  else
    xrandr --current --nograb >"${out_file}" 2>"${err_file}"
  fi
  rc=$?
  if [[ "${rc}" -eq 124 || "${rc}" -eq 137 ]]; then
    session_debug "xrandr: --current BLOCCATA e uccisa dal timeout (exit=${rc})"
    return 1
  fi
  if [[ "${rc}" -ne 0 ]]; then
    session_debug "xrandr: --current FALLITA exit=${rc}"
    return 1
  fi
  if [[ ! -s "${out_file}" ]]; then
    session_debug "xrandr: --current output vuoto"
    return 1
  fi
  session_debug "xrandr: --current OK ($(wc -c <"${out_file}" | tr -d ' ') byte)"
  return 0
}

session_xrandr() {
  session_ensure_x11
  local rc=0 out=""
  # --current (NON --query): riporta lo stato attuale gia' noto al server X
  # SENZA re-interrogare l'hardware (EDID/DDC dei monitor). E' proprio la
  # sonda hardware di --query che in certi ambienti fa restare xrandr
  # appeso. --current e' immediato e non si blocca.
  session_debug "xrandr: avvio --current --nograb (timeout -k 3 8) DISPLAY=${DISPLAY} XAUTHORITY=${XAUTHORITY}"
  out="$(session_xrandr_timeout --current --nograb 2>&1)"
  rc=$?
  if [[ "${rc}" -eq 0 ]]; then
    session_debug "xrandr: --current OK (${#out} byte)"
    printf '%s\n' "${out}"
    return 0
  fi
  if [[ "${rc}" -eq 124 || "${rc}" -eq 137 ]]; then
    session_debug "xrandr: --current BLOCCATA e uccisa dal timeout (exit=${rc})"
  else
    session_debug "xrandr: --current FALLITA exit=${rc}"
  fi
  [[ -n "${out}" ]] && session_debug "xrandr: stderr/stdout: ${out}"
  return "${rc}"
}

session_xrandr_active() {
  session_ensure_x11
  local rc=0 out=""
  session_debug "xrandr: avvio --listactivemonitors (timeout -k 3 8)"
  out="$(session_xrandr_timeout --listactivemonitors 2>&1)"
  rc=$?
  if [[ "${rc}" -eq 0 ]]; then
    session_debug "xrandr: listactivemonitors OK (${#out} byte)"
    printf '%s\n' "${out}"
    return 0
  fi
  session_debug "xrandr: listactivemonitors FALLITA exit=${rc}"
  [[ -n "${out}" ]] && session_debug "xrandr: stderr/stdout: ${out}"
  return "${rc}"
}

session_debug_cmd() {
  local label="$1"
  shift
  session_debug ">>> CMD [${label}]: $*"
  local out rc
  set +e
  out="$("$@" 2>&1)"
  rc=$?
  if [[ -n "${out}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      session_debug "    | ${line}"
    done <<<"${out}"
  else
    session_debug "    | (nessun output)"
  fi
  session_debug "<<< EXIT [${label}]=${rc}"
  return "${rc}"
}

session_debug_path() {
  local label="$1" path="$2" maxdepth="${3:-4}"
  session_debug "--- PATH [${label}]: ${path} ---"
  if [[ ! -e "${path}" ]]; then
    session_debug "    NON ESISTE"
    return 1
  fi
  session_debug_cmd "stat-${label}" stat "${path}" || true
  session_debug_cmd "du-${label}" du -sh "${path}" || true
  session_debug_cmd "find-${label}" find "${path}" -maxdepth "${maxdepth}" -print 2>/dev/null || true
  if [[ -d "${path}" ]]; then
    session_debug_cmd "find-files-${label}" sh -c "find '${path}' -type f 2>/dev/null | wc -l"
  fi
  return 0
}

session_debug_store_tree() {
  session_debug "========== ALBERO STORE =========="
  if [[ ! -d "${STORE}" ]]; then
    session_debug "STORE NON ESISTE: ${STORE}"
    return 1
  fi
  session_debug_cmd "store-du" du -sh "${STORE}"
  session_debug_cmd "store-find" find "${STORE}" -print 2>/dev/null
  if [[ -f "${STORE}/.quelo-last-save" ]]; then
    session_debug "ultimo salvataggio: $(cat "${STORE}/.quelo-last-save")"
  fi
  if [[ -f "${STORE}/.quelo-save-mode" ]]; then
    session_debug "modalita salvataggio: $(cat "${STORE}/.quelo-save-mode")"
  fi
  return 0
}

session_mount_persistence() {
  session_debug "Montaggio partizione ${PERSIST_LABEL}..."
  if ! blkid -L "${PERSIST_LABEL}" >/dev/null 2>&1; then
    session_debug "ERRORE: blkid non trova label ${PERSIST_LABEL}"
    return 1
  fi
  mkdir -p "${PERSIST_MNT}"
  if mountpoint -q "${PERSIST_MNT}" 2>/dev/null; then
    session_debug "Gia montata su ${PERSIST_MNT}"
  else
    session_debug_cmd "mount" mount -L "${PERSIST_LABEL}" "${PERSIST_MNT}" || return 1
  fi
  session_debug_enable_on_persist
  session_debug_cmd "mountpoint" mountpoint "${PERSIST_MNT}"
  session_debug_cmd "df-after-mount" df -h "${PERSIST_MNT}"
  return 0
}

session_umount_on_exit() {
  sync 2>/dev/null || true
  session_debug "sync completato (trap EXIT)"
  session_debug_store_tree || true
  session_debug "========== FINE DEBUG SISTEMA =========="
  if mountpoint -q "${PERSIST_MNT}" 2>/dev/null; then
    session_debug_cmd "umount" umount "${PERSIST_MNT}" || session_debug_cmd "umount-lazy" umount -l "${PERSIST_MNT}" || true
  fi
}

session_mirror_dir() {
  local label="$1" src="$2" dst="$3"
  session_debug "=== MIRROR [${label}] ==="
  session_debug_path "SRC-prima" "${src}" 5
  if [[ ! -e "${src}" ]]; then
    session_debug "MIRROR [${label}] SALTATO: sorgente assente"
    return 1
  fi
  session_debug_cmd "rm-rf-dst" rm -rf "${dst}"
  session_debug_cmd "mkdir-dst" mkdir -p "$(dirname "${dst}")"
  session_debug_cmd "cp-a" cp -av "${src}" "${dst}"
  local rc=$?
  session_debug_path "DST-dopo" "${dst}" 5
  session_debug "MIRROR [${label}] exit=${rc}"
  return "${rc}"
}

session_copy_file() {
  local label="$1" src="$2" dst="$3"
  session_debug "=== COPY FILE [${label}] ==="
  session_debug_path "SRC" "${src}" 1
  if [[ ! -f "${src}" ]]; then
    session_debug "COPY [${label}] SALTATO: file assente"
    return 1
  fi
  session_debug_cmd "mkdir-dst" mkdir -p "$(dirname "${dst}")"
  session_debug_cmd "cp-file" cp -av "${src}" "${dst}"
  local rc=$?
  session_debug_path "DST" "${dst}" 1
  return "${rc}"
}

# --- Firefox: backup/restore intero ~/.mozilla (come da manuale Mozilla) ---
# Es. tar -czvf backup_mozilla.tar.gz ~/.mozilla/
# Include profiles.ini, profili, preferiti, cronologia, login (logins.json+key4.db), ecc.

firefox_quit_if_running() {
  local waited=0
  pgrep -x firefox-esr >/dev/null 2>&1 || pgrep -x firefox >/dev/null 2>&1 || return 0
  session_debug "Chiusura Firefox (necessaria prima del backup/restore)..."
  pkill -TERM -x firefox-esr 2>/dev/null || true
  pkill -TERM -x firefox 2>/dev/null || true
  while { pgrep -x firefox-esr >/dev/null 2>&1 || pgrep -x firefox >/dev/null 2>&1; } && [[ "${waited}" -lt 20 ]]; do
    sleep 1
    waited=$((waited + 1))
  done
  if pgrep -x firefox-esr >/dev/null 2>&1 || pgrep -x firefox >/dev/null 2>&1; then
    pkill -KILL -x firefox-esr 2>/dev/null || true
    pkill -KILL -x firefox 2>/dev/null || true
    sleep 1
  fi
}

session_backup_mozilla_home() {
  local archive="$1"
  local mozilla_root="/root/.mozilla"

  session_debug "=== BACKUP FIREFOX ~/.mozilla -> ${archive} ==="
  [[ -d "${mozilla_root}" ]] || return 1

  firefox_quit_if_running
  session_debug_cmd "mkdir-archive" mkdir -p "$(dirname "${archive}")"
  # Esclude solo cache voluminosa; il resto (profili, sqlite, login) resta incluso.
  session_debug_cmd "tar-backup-mozilla" tar -czf "${archive}" -C /root \
    --exclude='.mozilla/firefox/*/cache2' \
    --exclude='.mozilla/firefox/*/*/cache2' \
    --exclude='.mozilla/firefox/*/startupCache' \
    --exclude='.mozilla/firefox/*/*/startupCache' \
    --exclude='.mozilla/firefox/*/OfflineCache' \
    --exclude='.mozilla/firefox/*/*/OfflineCache' \
    .mozilla
  [[ -f "${archive}" && -s "${archive}" ]]
}

session_restore_mozilla_home() {
  local archive="$1"
  local mozilla_root="/root/.mozilla"

  session_debug "=== RESTORE FIREFOX ${archive} -> ~/.mozilla ==="
  [[ -f "${archive}" && -s "${archive}" ]] || return 1

  firefox_quit_if_running
  if [[ -d "${mozilla_root}" ]]; then
    session_debug_cmd "mv-old-mozilla" mv "${mozilla_root}" "${mozilla_root}.quelo-bak.$(date +%s)" 2>/dev/null || true
  fi
  session_debug_cmd "tar-restore-mozilla" tar -xzf "${archive}" -C /root
  [[ -d "${mozilla_root}" ]]
}

: >"${SESSION_TMP_DEBUG}" 2>/dev/null || true
