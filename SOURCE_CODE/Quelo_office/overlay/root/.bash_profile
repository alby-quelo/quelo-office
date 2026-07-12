# Quelo Office: avvio automatico del desktop grafico dopo l'autologin
# testuale su tty1 (gestito da live-config, vedi LIVE_AUTOLOGIN in
# etc/live/config.conf.d/quelo-office.conf). Niente "exec": se X si chiude
# (crash del driver video o scelta "Esci" dal menu, che fa "openbox --exit"),
# si ricade su un prompt di shell utilizzabile come fallback manuale invece
# di un loop di riavvii infiniti se qualcosa va storto.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
if [ -z "${DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
  startx
fi
