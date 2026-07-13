#!/bin/bash
# Quelo Office — prepara chiavetta USB (dd ISO + persistenza + home exFAT)
# Esegui SOLO dal PC locale, MAI dalla live bootata sulla stessa USB.
#
# Uso: sudo ./prepare-usb.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ISO_DIR="${PROJECT_DIR}/ISO"
# ISO pubblicata/distribuita (unica da caricare online). Non usare sort -V "ultima"
# se in locale restano ISO di sviluppo più recenti.
QUELO_PUBLISH_ISO_VERSION="0.71"
QUELO_PUBLISH_ISO="${ISO_DIR}/Quelo_Office-${QUELO_PUBLISH_ISO_VERSION}-alpha.iso"

PERSIST_LABEL="persistence"
HOME_LABEL="QUELO-HOME"
TOTAL_STEPS=9

PERSIST_PART=""
HOME_PART=""

# --- utilità ---

quelo_die() {
  echo ""
  echo "ERRORE: $*"
  exit 1
}

quelo_step() {
  local n="$1"
  local msg="$2"
  echo ""
  echo "============================================================"
  printf " PASSO %s/%s — %s\n" "${n}" "${TOTAL_STEPS}" "${msg}"
  echo "============================================================"
}

quelo_pause() {
  read -r -p ">>> Premi INVIO per continuare (oppure Ctrl+C per annullare)... " _
}

quelo_part_suffix() {
  local disk="$1"
  if [[ "${disk}" == /dev/nvme* || "${disk}" == /dev/mmcblk* ]]; then
    printf 'p'
  else
    printf ''
  fi
}

quelo_set_part_paths() {
  local disk="$1"
  local sfx
  sfx=$(quelo_part_suffix "${disk}")
  PERSIST_PART="${disk}${sfx}3"
  HOME_PART="${disk}${sfx}4"
}

quelo_root_disk() {
  local src pk
  src=$(findmnt -no SOURCE / 2>/dev/null || true)
  [[ -n "${src}" ]] || return 1
  pk=$(lsblk -no PKNAME "${src}" 2>/dev/null || true)
  [[ -n "${pk}" ]] || return 1
  printf '/dev/%s\n' "${pk}"
}

quelo_disk_is_usb() {
  local disk="$1" base rem tran bus
  base=${disk#/dev/}
  rem="/sys/block/${base}/removable"
  [[ -f "${rem}" && "$(cat "${rem}")" == "1" ]] && return 0
  tran=$(lsblk -dn -o TRAN "${disk}" 2>/dev/null || true)
  [[ "${tran}" == "usb" ]] && return 0
  bus=$(udevadm info -q property "${disk}" 2>/dev/null | awk -F= '$1=="ID_BUS"{print $2; exit}')
  [[ "${bus}" == "usb" ]] && return 0
  return 1
}

quelo_find_publish_iso() {
  if [[ -f "${QUELO_PUBLISH_ISO}" ]]; then
    printf '%s\n' "${QUELO_PUBLISH_ISO}"
    return 0
  fi
  return 1
}

quelo_find_latest_iso() {
  quelo_find_publish_iso
}

quelo_confirm_usb() {
  local disk="$1"
  local name="${disk#/dev/}"
  local desc typed

  desc=$(lsblk -d -o NAME,SIZE,MODEL,TRAN "${disk}" 2>/dev/null | tail -1)

  echo ""
  echo "DISCO SELEZIONATO:"
  echo "  ${desc}"
  echo ""
  echo "Tutti i dati su questo disco verranno SOVRASCRITTI."
  echo ""

  read -r -p "Conferma 1/2 — digita il nome disco (es. ${name}): " typed
  [[ "${typed}" == "${name}" ]] || quelo_die "Conferma errata. Operazione annullata."

  read -r -p "Conferma 2/2 — digita: SI SCRIVI : " typed
  [[ "${typed^^}" == "SI SCRIVI" ]] || quelo_die "Conferma errata. Operazione annullata."
}

quelo_umount_usb() {
  # IMPORTANTE: smonta SOLO le partizioni del device passato come
  # argomento (mai altri dischi). Usa il mountpoint reale riportato
  # da lsblk (non il device stesso: "mountpoint /dev/sdaX" è sempre
  # falso perché mountpoint vuole una directory, non un device).
  local disk="$1" dev mp

  while read -r dev mp; do
    [[ -n "${dev}" ]] || continue
    [[ -n "${mp}" ]] || continue
    echo "Smonto ${dev} (${mp})..."
    umount "${dev}" 2>/dev/null \
      || umount -l "${dev}" 2>/dev/null \
      || umount "${mp}" 2>/dev/null \
      || umount -l "${mp}" 2>/dev/null \
      || true
  done < <(lsblk -pln -o NAME,MOUNTPOINT "${disk}" | awk '$1 ~ /^'"${disk//\//\\/}"'/ && $2!=""{print $1, $2}')
}

quelo_swapoff_usb() {
  local disk="$1" p
  while read -r p; do
    [[ -n "${p}" ]] || continue
    swapoff "${p}" 2>/dev/null || true
  done < <(lsblk -ln -o NAME,TYPE,FSTYPE "${disk}" | awk '$2=="part" && $3=="swap"{print "/dev/"$1}')
}

quelo_wipe_usb() {
  local disk="$1"
  echo "Smonto tutte le partizioni su ${disk}..."
  quelo_umount_usb "${disk}"
  quelo_swapoff_usb "${disk}"
  echo "Cancello firme e tabella partizioni (wipefs)..."
  wipefs -af "${disk}"
  sync

  # Il kernel a volte tiene "in memoria" vecchie partizioni anche dopo
  # wipefs. Forziamo la rilettura per non trovarci partizioni fantasma
  # (es. una vecchia sda4) dopo aver scritto la nuova ISO.
  partprobe "${disk}" 2>/dev/null || true
  blockdev --rereadpt "${disk}" 2>/dev/null || true
  if command -v partx >/dev/null 2>&1; then
    partx -d "${disk}" >/dev/null 2>&1 || true
  fi
  sleep 2
  echo "Chiavetta azzerata: pronta per scrittura ISO pulita."
}

quelo_settle_usb_before_partition() {
  # Il file manager (MATE/Caja e simili) rimonta da solo le nuove
  # partizioni appena appaiono (es. subito dopo il dd). Prima di
  # partizionare dobbiamo risbloccare il disco: smontiamo SOLO le
  # partizioni di questo device USB (mai altri dischi).
  local disk="$1"
  local tries=0

  while (( tries < 5 )); do
    quelo_umount_usb "${disk}"
    quelo_swapoff_usb "${disk}"
    sleep 1
    if ! lsblk -ln -o NAME,MOUNTPOINT "${disk}" | awk '$2!=""{found=1} END{exit !found}'; then
      return 0
    fi
    tries=$(( tries + 1 ))
    sleep 1
  done
  return 1
}

quelo_progress_bar() {
  local cur="$1"
  local total="$2"
  local label="$3"
  local pct bar_width=40 filled empty

  (( total > 0 )) || total=1
  pct=$(( cur * 100 / total ))
  (( pct > 100 )) && pct=100
  filled=$(( pct * bar_width / 100 ))
  empty=$(( bar_width - filled ))
  printf '\r['
  printf '%*s' "${filled}" '' | tr ' ' '#'
  printf '%*s' "${empty}" '' | tr ' ' '-'
  printf '] %3d%%  %s/%s  %s   ' "${pct}" "${cur}" "${total}" "${label}"
}

quelo_progress_step() {
  quelo_progress_bar "$1" "$2" "$3"
  echo ""
}

quelo_human() {
  local bytes="$1"
  numfmt --to=iec-i --suffix=B "${bytes}" 2>/dev/null || printf '%sB' "${bytes}"
}

quelo_write_iso() {
  local ifile="$1"
  local ofile="$2"
  local writer="${SCRIPT_DIR}/quelo-write-iso.py"

  if [[ -f "${writer}" ]] && command -v python3 >/dev/null 2>&1; then
    python3 "${writer}" "${ifile}" "${ofile}" || quelo_die "scrittura/verifica ISO fallita."
    return 0
  fi

  echo "python3/quelo-write-iso.py non disponibile, fallback dd..."
  quelo_progress_dd "${ifile}" "${ofile}"
  if [[ -f "${writer}" ]] && command -v python3 >/dev/null 2>&1; then
    python3 "${writer}" --verify-only "${ifile}" "${ofile}" || quelo_die "verifica ISO fallita."
  fi
}

quelo_progress_dd() {
  # Scrittura aggressiva su block device: buffer grande, I/O diretto,
  # sync solo a fine (come Etcher). Progresso da /sys/block/<dev>/stat.
  local ifile="$1"
  local ofile="$2"
  local dd_bs="64M"
  local base stat_file isize start_sectors cur_sectors written pct
  local bar_width=40 filled empty t0 elapsed pid rc
  local -a dd_extra=()

  base=$(basename "${ofile}")
  stat_file="/sys/block/${base}/stat"
  isize=$(stat -c%s "${ifile}")

  echo ""
  echo "Scrittura ISO su USB: $(quelo_human "${isize}") totali."
  echo "Modalita aggressiva: bs=${dd_bs}, oflag=direct, sync a fine."
  echo "NON rimuovere la USB fino a 'Scrittura completata'."
  echo ""

  blockdev --setra 16384 "${ofile}" 2>/dev/null || true

  quelo_dd_run() {
    dd if="${ifile}" of="${ofile}" bs="${dd_bs}" status=none "${dd_extra[@]}" &
    pid=$!
  }

  dd_extra=(oflag=direct)
  quelo_dd_run

  start_sectors=0
  [[ -r "${stat_file}" ]] && start_sectors=$(awk '{print $7}' "${stat_file}" 2>/dev/null || echo 0)
  t0=$(date +%s)

  while kill -0 "${pid}" 2>/dev/null; do
    sleep 1
    written=0
    if [[ -r "${stat_file}" ]]; then
      cur_sectors=$(awk '{print $7}' "${stat_file}" 2>/dev/null || echo "${start_sectors}")
      written=$(( (cur_sectors - start_sectors) * 512 ))
      (( written < 0 )) && written=0
      (( written > isize )) && written=${isize}
    fi
    pct=0
    (( isize > 0 )) && pct=$(( written * 100 / isize ))
    (( pct > 100 )) && pct=100
    filled=$(( pct * bar_width / 100 ))
    empty=$(( bar_width - filled ))
    elapsed=$(( $(date +%s) - t0 ))
    printf '\r['
    printf '%*s' "${filled}" '' | tr ' ' '#'
    printf '%*s' "${empty}" '' | tr ' ' '-'
    printf '] %3d%%  %s / %s  (%ds)   ' \
      "${pct}" "$(quelo_human "${written}")" "$(quelo_human "${isize}")" "${elapsed}"
  done

  wait "${pid}"
  rc=$?

  if (( rc != 0 )) && [[ "${dd_extra[*]}" == *direct* ]]; then
    echo ""
    echo "oflag=direct fallito, retry con buffer grande..."
    dd_extra=()
    quelo_dd_run
    while kill -0 "${pid}" 2>/dev/null; do
      sleep 1
    done
    wait "${pid}"
    rc=$?
  fi

  echo ""
  if (( rc != 0 )); then
    quelo_die "dd terminato con errore (exit ${rc})."
  fi

  echo -n "Sincronizzazione dispositivo... "
  blockdev --flushbufs "${ofile}" 2>/dev/null || true
  sync
  echo "ok."
  echo "Scrittura completata."
}

quelo_reread_partition_table() {
  local disk="$1"
  partprobe "${disk}" 2>/dev/null || true
  blockdev --rereadpt "${disk}" 2>/dev/null || true
  sleep 2
}

quelo_show_usb() {
  lsblk -o NAME,SIZE,FSTYPE,LABEL,TYPE,MOUNTPOINT "${USB}"
}

quelo_verify_new_partitions() {
  local psize

  quelo_set_part_paths "${USB}"

  if [[ ! -b "${PERSIST_PART}" || ! -b "${HOME_PART}" ]]; then
    quelo_die "Non vedo ${PERSIST_PART} e/o ${HOME_PART} dopo la creazione partizioni."
  fi

  psize=$(lsblk -bn -o SIZE "${PERSIST_PART}" 2>/dev/null || echo 0)
  if [[ "${psize}" -le 0 ]]; then
    quelo_die "Dimensione ${PERSIST_PART} non valida (${psize} byte)."
  fi
}

# --- main ---

[[ "${EUID}" -eq 0 ]] || quelo_die "Esegui come root: sudo $0"

if grep -q ' boot=live' /proc/cmdline 2>/dev/null; then
  quelo_die "Sei in sessione LIVE. Esegui questo script dal PC locale, non dalla USB avviata."
fi

command -v wipefs >/dev/null || quelo_die "wipefs mancante (pacchetto util-linux)."
command -v fdisk >/dev/null || quelo_die "fdisk mancante (pacchetto util-linux)."
command -v mkfs.ext4 >/dev/null || quelo_die "mkfs.ext4 mancante (pacchetto e2fsprogs)."
command -v mkfs.exfat >/dev/null || quelo_die "mkfs.exfat mancante (pacchetto exfatprogs)."

clear
echo "Quelo Office — preparazione chiavetta USB"
echo "========================================"

# PASSO 1: ISO
quelo_step 1 "Seleziona ISO"

ISO="$(quelo_find_publish_iso || true)"
if [[ -n "${ISO}" ]]; then
  echo "ISO di pubblicazione (${QUELO_PUBLISH_ISO_VERSION}-alpha): ${ISO}"
  read -r -p "Usarla? [S/n]: " ans
  if [[ "${ans,,}" == n ]]; then
    ISO=""
  fi
fi

if [[ -z "${ISO}" ]]; then
  read -r -p "Percorso completo file .iso: " ISO
fi

[[ -f "${ISO}" ]] || quelo_die "ISO non trovata: ${ISO}"
echo "OK: $(du -h "${ISO}" | awk '{print $1}') — ${ISO}"
quelo_pause

# PASSO 2: disco USB
quelo_step 2 "Seleziona disco USB"

echo "Dischi nel sistema:"
lsblk -d -o NAME,SIZE,MODEL,TRAN,TYPE
echo ""

ROOT_DISK="$(quelo_root_disk || true)"
[[ -n "${ROOT_DISK}" ]] && echo "Disco di sistema (NON usare): ${ROOT_DISK}"
echo ""

read -r -p "Device USB (es. /dev/sda): " USB
[[ "${USB}" == /dev/* ]] || USB="/dev/${USB}"
[[ -b "${USB}" ]] || quelo_die "Device non valido: ${USB}"

[[ "${USB}" != "${ROOT_DISK}" ]] || quelo_die "Hai scelto il disco di sistema. STOP."

quelo_disk_is_usb "${USB}" || {
  echo "ATTENZIONE: il disco non risulta USB/removable."
  read -r -p "Continuare comunque? [s/N]: " ans
  [[ "${ans,,}" == s ]] || quelo_die "Operazione annullata."
}

quelo_confirm_usb "${USB}"
quelo_pause

# PASSO 3: dimensione persistenza
quelo_step 3 "Dimensione persistenza"

echo "Scegli dimensione partizione Linux (config + pacchetti installati):"
echo "  1) 512 MB"
echo "  2) 1024 MB (consigliato)"
echo "  3) 2048 MB"
read -r -p "Scelta [1-3, default 2]: " choice
case "${choice:-2}" in
  1) PERSIST_MB=512 ;;
  2) PERSIST_MB=1024 ;;
  3) PERSIST_MB=2048 ;;
  *) quelo_die "Scelta non valida." ;;
esac
echo "Persistenza: ${PERSIST_MB} MB"
quelo_pause

# PASSO 4: wipe tabella partizioni
quelo_step 4 "Pulizia chiavetta (cancella vecchie partizioni)"

echo "Stato attuale:"
quelo_show_usb
echo ""
echo "Ora cancello TUTTE le partizioni/firme su ${USB}."
quelo_pause
quelo_wipe_usb "${USB}"
echo ""
echo "Dopo pulizia:"
quelo_show_usb || echo "(disco vuoto — normale)"
quelo_pause

# PASSO 5: dd
quelo_step 5 "Scrittura ISO (operazione lunga)"

quelo_write_iso "${ISO}" "${USB}"
sync
quelo_reread_partition_table "${USB}"

echo ""
echo "Dopo scrittura ISO:"
quelo_show_usb
quelo_pause

# PASSO 6: partizioni
quelo_step 6 "Creazione partizioni persistenza + home"

echo ""
echo "Verranno create:"
echo "  - ${USB}3  (Linux, ~${PERSIST_MB} MiB)  → persistenza"
echo "  - ${USB}4  (Linux, resto del disco)     → home exFAT"
echo ""

echo "Sblocco il disco (smonto SOLO ${USB}, nessun altro disco)..."
if ! quelo_settle_usb_before_partition "${USB}"; then
  echo "ATTENZIONE: non riesco a smontare completamente ${USB} (il file manager potrebbe rimontarlo da solo)."
  echo "Stato attuale:"
  quelo_show_usb
  quelo_pause
fi

AUTO_PART=0
if command -v sfdisk >/dev/null 2>&1; then
  echo "Lo script può creare queste partizioni automaticamente (sfdisk)."
  read -r -p "Procedo automaticamente? [S/n]: " ans
  [[ "${ans,,}" != n ]] && AUTO_PART=1
else
  echo "sfdisk non trovato: si procede solo in modalità manuale."
fi

if [[ "${AUTO_PART}" -eq 1 ]]; then
  echo ""
  sf_ok=0
  for attempt in 1 2 3; do
    echo "Creo le partizioni con sfdisk (tentativo ${attempt}/3)..."
    quelo_settle_usb_before_partition "${USB}" || true
    if printf ',%sM,L\n,,7\n' "${PERSIST_MB}" | sfdisk --append "${USB}"; then
      sf_ok=1
      break
    fi
    echo "sfdisk non riuscito (disco probabilmente rimontato dal file manager). Riprovo..."
    sleep 2
  done

  if [[ "${sf_ok}" -eq 1 ]]; then
    quelo_reread_partition_table "${USB}"
    echo ""
    echo "Risultato:"
    quelo_show_usb
    if ! { quelo_verify_new_partitions; }; then
      echo ""
      echo "La creazione automatica non ha dato il risultato atteso."
      AUTO_PART=0
    fi
  else
    echo ""
    echo "sfdisk ha restituito un errore dopo 3 tentativi."
    AUTO_PART=0
  fi
fi

if [[ "${AUTO_PART}" -eq 0 ]]; then
  echo ""
  echo "Procedura MANUALE — crea tu le partizioni con fdisk."
  echo ""
  quelo_settle_usb_before_partition "${USB}" || true
  echo "Sequenza consigliata in fdisk:"
  echo "  n  (nuova partizione)"
  echo "  p  (primary)"
  echo "  3"
  echo "  Invio  (first sector di default)"
  echo "  +${PERSIST_MB}M"
  echo ""
  echo "  n"
  echo "  p"
  echo "  4"
  echo "  Invio"
  echo "  Invio  (fino a fine disco)"
  echo ""
  echo "  w  (scrivi e esci)"
  echo ""
  quelo_pause

  fdisk "${USB}"

  echo ""
  echo "Rileggo tabella partizioni..."
  quelo_reread_partition_table "${USB}"

  echo ""
  echo "Controllo risultato:"
  quelo_show_usb
  quelo_verify_new_partitions
fi

echo ""
echo "Partizioni pronte:"
quelo_show_usb
echo ""
echo "Persistenza: ${PERSIST_PART}"
echo "Home exFAT:  ${HOME_PART}"
quelo_pause

# PASSO 7: formattazione
quelo_step 7 "Formattazione partizioni"

echo "ATTENZIONE: sto per formattare ${PERSIST_PART} e ${HOME_PART}."
quelo_pause

echo "Formatto persistenza (ext4, label ${PERSIST_LABEL})..."
mkfs.ext4 -F -L "${PERSIST_LABEL}" "${PERSIST_PART}"

echo "Formatto home (exFAT, label ${HOME_LABEL})..."
mkfs.exfat -n "${HOME_LABEL}" "${HOME_PART}"
quelo_pause

# PASSO 8: cartelle home exFAT (partizione persistenza resta VUOTA)
quelo_step 8 "Cartelle home exFAT"

CONFIG_TOTAL=4
CONFIG_STEP=0

HOME_MNT=$(mktemp -d)

CONFIG_STEP=$(( CONFIG_STEP + 1 ))
mount "${HOME_PART}" "${HOME_MNT}"
quelo_progress_step "${CONFIG_STEP}" "${CONFIG_TOTAL}" "Monta home exFAT"

CONFIG_STEP=$(( CONFIG_STEP + 1 ))
mkdir -p "${HOME_MNT}/home"/{Desktop,Documenti,Scaricati,Immagini,Musica,Video,Modelli}
quelo_progress_step "${CONFIG_STEP}" "${CONFIG_TOTAL}" "Crea cartelle home"

CONFIG_STEP=$(( CONFIG_STEP + 1 ))
mkdir -p "${HOME_MNT}/quelo-export"
date -Iseconds >"${HOME_MNT}/home/.quelo-prepared"
python3 - "${HOME_MNT}" "${SCRIPT_DIR}" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
import quelo_prepare_common as common
common.write_windows_boot_protect_files(sys.argv[1])
PY
sync
quelo_progress_step "${CONFIG_STEP}" "${CONFIG_TOTAL}" "Crea quelo-export"

CONFIG_STEP=$(( CONFIG_STEP + 1 ))
umount "${HOME_MNT}"
rmdir "${HOME_MNT}"
quelo_progress_step "${CONFIG_STEP}" "${CONFIG_TOTAL}" "Smonta home exFAT"

echo "Configurazione completata."

# PASSO 9: riepilogo
quelo_step 9 "Completato"

echo ""
echo "Chiavetta pronta."
echo ""
quelo_show_usb
echo ""
echo "Prossimi passi:"
echo "  1. Rimuovi la USB in sicurezza"
echo "  2. Boot da USB"
echo "  3. Test persistenza rete: connetti 4G, reboot, verifica connessione"
echo "  4. Test persistenza apt: apt install htop && reboot && which htop"
echo "  5. Export modifiche per rebuild: quelo-export"
echo ""
echo "Label attese:"
echo "  - ${PERSIST_LABEL}  (ext4, Linux)"
echo "  - ${HOME_LABEL}     (exFAT, Windows/Mac/Linux)"
echo ""
