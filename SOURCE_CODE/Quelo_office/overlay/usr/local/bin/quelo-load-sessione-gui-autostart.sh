#!/bin/bash
# Avvio load GUI dopo desktop LXQt: attende X, pannello, persistence, poi +2 s.
LOG="/tmp/quelo-load-gui-autostart.log"
exec 9>/tmp/quelo-load-sessione-gui.lock
flock -n 9 || exit 0
exec >>"${LOG}" 2>&1
echo "=== $(date -Iseconds) autostart load GUI pid=$$ ==="

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/root/.Xauthority}"

PERSIST_MNT="/media/quelo-persist"

for _ in $(seq 1 120); do
  xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && break
  sleep 1
done

for _ in $(seq 1 120); do
  pgrep -x lxqt-panel >/dev/null 2>&1 && break
  sleep 1
done

for _ in $(seq 1 45); do
  mountpoint -q "${PERSIST_MNT}" 2>/dev/null && break
  sleep 1
done

sleep 2

echo "Avvio load GUI DISPLAY=${DISPLAY}"
exec /usr/local/bin/quelo-load-sessione-gui.sh
