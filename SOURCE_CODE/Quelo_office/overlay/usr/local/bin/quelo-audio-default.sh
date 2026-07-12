#!/bin/bash
# Volume audio di default: 60%, non muto, ad ogni avvio.
#
# Due livelli distinti (entrambi servono):
#   1) ALSA (hardware) - chiamato PRIMA di "pulseaudio --start"
#   2) PulseAudio (sink) - chiamato DOPO con "--pulse"
#
# CRITICO: l'icona volume del pannello LXQt legge il sink PulseAudio
# (pactl), NON i controlli ALSA (amixer). Lo script precedente impostava
# solo amixer al 60% e basta: PulseAudio partiva con il SUO volume (spesso
# basso, es. 13% in barra) indipendente da ALSA - da qui il bug che
# l'utente vedeva 13% invece di 60%.
set -uo pipefail

TARGET_VOL="60%"

quelo_audio_default_alsa() {
  alsactl init >/dev/null 2>&1 || true

  if ! command -v amixer >/dev/null 2>&1; then
    return 0
  fi

  while IFS= read -r ctrl; do
    case "${ctrl}" in
      Capture*|Mic*|Internal*|Loopback*|Beep*|Auto-Mute*)
        continue
        ;;
    esac
    amixer -q sset "${ctrl}" unmute 2>/dev/null || true
    amixer -q sset "${ctrl}" "${TARGET_VOL}" 2>/dev/null || true
  done < <(amixer scontrols 2>/dev/null | sed -n "s/^Simple mixer control '\([^']*\)',.*/\1/p")
}

quelo_audio_default_pulse() {
  if ! command -v pactl >/dev/null 2>&1; then
    return 0
  fi

  pactl info >/dev/null 2>&1 || return 0

  pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null || true
  pactl set-sink-volume @DEFAULT_SINK@ "${TARGET_VOL}" 2>/dev/null || true

  while IFS= read -r sink; do
    [[ -n "${sink}" ]] || continue
    pactl set-sink-mute "${sink}" 0 2>/dev/null || true
    pactl set-sink-volume "${sink}" "${TARGET_VOL}" 2>/dev/null || true
  done < <(pactl list short sinks 2>/dev/null | awk '{print $2}')
}

case "${1:-}" in
  --pulse)
    quelo_audio_default_pulse
    ;;
  *)
    quelo_audio_default_alsa
    ;;
esac

exit 0
