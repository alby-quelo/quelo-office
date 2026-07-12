#!/bin/sh
# XDG_RUNTIME_DIR: normalmente creata/esportata da pam_systemd o elogind
# al login. Qui l'autologin passa da getty+startx, senza un gestore di
# sessione PAM dedicato, quindi resterebbe sempre non impostata. Molte
# app moderne la richiedono per funzionare bene (PulseAudio incluso: e'
# una causa nota di comportamenti "rotti" senza errori espliciti, come
# l'icona del volume che resta sulla "x" rossa nel pannello).
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"

# Sblocca/porta a volume udibile i canali ALSA PRIMA di avviare
# PulseAudio - vedi quelo-audio-default.sh per il perche' (schede audio
# che partono mute/a 0 per default hardware, indipendentemente da
# qualunque persistenza).
/usr/local/bin/quelo-audio-default.sh

# Avvio esplicito di PulseAudio (invece di contare solo sull'autospawn
# alla prima connessione client): "--start" e' sincrono, ritorna solo a
# demone gia' su e pronto, cosi' il plugin volume del pannello (avviato
# poco dopo da startlxqt) lo trova sempre gia' disponibile, eliminando
# la corsa che lo lasciava bloccato sull'icona "rotta".
pulseaudio --start 2>/dev/null

# 60% sul sink PulseAudio: e' QUESTO che mostra la barra del pannello,
# non i controlli ALSA impostati sopra (vedi quelo-audio-default.sh).
/usr/local/bin/quelo-audio-default.sh --pulse

# Avvia la sessione LXQt (Openbox come window manager, vedi
# etc/xdg/lxqt/session.conf) esattamente come faceva SDDM con
# "Session=lxqt.desktop", ma lanciata direttamente da startx.
exec startlxqt
