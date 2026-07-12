#!/bin/bash
# Corregge SOLO l'etichetta (riga "Name=") della scorciatoia nativa "Home"
# che pcmanfm-qt crea da solo sul Desktop (DesktopShortcuts=Home, vedi
# settings.conf) - senza toccare Type/Exec/URL/Icon.
#
# CRITICO - perche' questo e non un file scritto a mano (tentativi
# precedenti, sempre falliti): pcmanfm-qt (funzione createHomeShortcut() in
# desktopwindow.cpp, codice sorgente ufficiale del progetto) rigenera SEMPRE
# da solo ~/Desktop/user-home.desktop ad ogni avvio del desktop, ogni volta
# che "Home" e' nell'elenco DesktopShortcuts - con Name=g_get_user_name()
# (quindi sempre "root", il nome dell'utente unix), e con Exec calcolato al
# volo da Fm::FilePath::homeDir(). La "fiducia" (poter fare doppio click e
# farlo funzionare, invece di farlo aprire come testo/LibreOffice) NON
# dipende da "gio set metadata::trust" ne' da chmod +x per questo file
# particolare: pcmanfm-qt lo considera fidato solo se il campo Exec sul
# disco combacia esattamente con quello che genererebbe lui in quel
# momento - un controllo di stringa interno, in memoria. Se scriviamo un
# file statico PRIMA che parta, lui lo sovrascrive comunque all'avvio; se
# lo modifichiamo cambiando anche Exec/URL perdiamo la fiducia. Quindi:
# lasciamo creare il file a lui (Exec/URL/Icon restano intatti = resta
# fidato) e cambiamo SOLO Name=, dopo che l'ha scritto - esattamente
# l'effetto di un utente che fa tasto destro "Rinomina" sull'icona.
set -uo pipefail

DESKTOP_DIR="${HOME}/Desktop"
DESKTOP_FILE="${DESKTOP_DIR}/user-home.desktop"
TARGET_NAME="Home"

# Finestra di ~30s: pcmanfm-qt puo' (ri)scrivere il file piu' di una volta
# durante l'avvio (es. inizializzazione multi-monitor) - continuiamo a
# ricontrollare/correggere invece di uscire alla prima corrispondenza.
end=$((SECONDS + 30))
fixed_once=0
while ((SECONDS < end)); do
  if [[ -f "${DESKTOP_FILE}" ]] && grep -q '^Name=' "${DESKTOP_FILE}" 2>/dev/null; then
    if ! grep -qx "Name=${TARGET_NAME}" "${DESKTOP_FILE}" 2>/dev/null; then
      sed -i "s/^Name=.*/Name=${TARGET_NAME}/" "${DESKTOP_FILE}"
      fixed_once=1
      logger -t quelo-home-label-fix "etichetta icona Home impostata a '${TARGET_NAME}'"
    fi
  fi
  sleep 1
done

if [[ "${fixed_once}" -eq 0 ]] && [[ ! -f "${DESKTOP_FILE}" ]]; then
  logger -t quelo-home-label-fix "${DESKTOP_FILE} non trovato dopo 30s, nessuna correzione applicata"
fi

exit 0
