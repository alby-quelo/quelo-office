#!/bin/bash
# Genera uno script xrandr che ripristina il layout schermo corrente.
# Usato da salva_sessione (persistenza manuale).
#
# Uso:
#   quelo-display-capture.sh <file-output> [file-query]
#
# Se <file-query> e' fornito, viene usato come sorgente (output gia'
# catturato di `xrandr --query`) e NON viene invocato xrandr: cosi' si
# evita di ri-bloccare xrandr, che in certi ambienti resta appeso sulla
# connessione/grab del server X (vedi manuale: "xrandr just hangs").
set -o pipefail

out="${1:?file di output richiesto}"
query_file="${2:-}"
mkdir -p "$(dirname "${out}")"

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/root/.Xauthority}"

cmd_count=0

quelo_display_emit() {
  local name="$1" width="$2" height="$3" xpos="$4" ypos="$5" primary="$6" rotate="$7"
  printf "xrandr --output '%s' --mode '%sx%s' --pos %sx%s%s%s 2>/dev/null || xrandr --output '%s' --auto%s 2>/dev/null || true\n" \
    "${name}" "${width}" "${height}" "${xpos}" "${ypos}" "${primary}" "${rotate}" \
    "${name}" "${primary}"
  cmd_count=$((cmd_count + 1))
}

# Sorgente dell'output di `xrandr --current`: file passato o cattura al volo
# con timeout forzato (SIGKILL dopo ~11s se xrandr si blocca).
# NB: --current, non --query, per non re-interrogare l'hardware (evita i
# blocchi noti di xrandr sulla sonda EDID/DDC dei monitor).
quelo_query() {
  if [[ -n "${query_file}" && -f "${query_file}" ]]; then
    cat "${query_file}"
    return 0
  fi
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 3 8 xrandr --current --nograb 2>/dev/null
  else
    xrandr --current --nograb 2>/dev/null
  fi
}

{
  echo '#!/bin/bash'
  echo '# Layout schermo Quelo Office (quelo-display-capture.sh)'
  echo 'set +e'
  echo 'export DISPLAY="${DISPLAY:-:0}"'
  echo 'export XAUTHORITY="${XAUTHORITY:-/root/.Xauthority}"'
  echo 'sleep 1'

  # Parsing dall'output di `xrandr --current`: righe tipo
  #   HDMI-1 connected primary 1920x1080+0+0 (normal left ...) 509mm x 286mm
  #   eDP-1 connected 1080x1920+1920+0 left (normal left ...) ...
  # La rotazione ATTIVA (se non "normal") sta tra la geometria e la "(":
  # NON dentro la parentesi, che elenca sempre tutte le rotazioni possibili.
  QUERY="$(quelo_query)"

  while IFS= read -r line; do
    [[ "${line}" == *" connected"* ]] || continue
    name="${line%% *}"
    primary=""
    [[ "${line}" == *" primary"* ]] && primary=" --primary"
    if [[ "${line}" =~ ([0-9]+)x([0-9]+)\+([0-9]+)\+([0-9]+) ]]; then
      geom="${BASH_REMATCH[0]}"
      # Testo tra la geometria e la prima "(": contiene la rotazione attiva.
      after="${line#*"${geom}"}"
      before_paren="${after%%(*}"
      rotate=""
      case " ${before_paren} " in
        *" left "*)     rotate=" --rotate left" ;;
        *" right "*)    rotate=" --rotate right" ;;
        *" inverted "*) rotate=" --rotate inverted" ;;
      esac
      quelo_display_emit "${name}" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" \
        "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}" "${primary}" "${rotate}"
    fi
  done <<< "${QUERY}"

  # Output connessi ma spenti: li lasciamo spenti al ripristino.
  while IFS= read -r line; do
    [[ "${line}" == *" disconnected"* ]] || continue
    name="${line%% *}"
    printf "xrandr --output '%s' --off 2>/dev/null || true\n" "${name}"
  done <<< "${QUERY}"
} >"${out}.tmp"

chmod 755 "${out}.tmp"
mv -f "${out}.tmp" "${out}"

if [[ "${cmd_count}" -eq 0 ]]; then
  echo "ERRORE: nessun monitor attivo catturato (DISPLAY=${DISPLAY})" >&2
  exit 1
fi

echo "OK: ${cmd_count} comandi xrandr in ${out}" >&2
exit 0
