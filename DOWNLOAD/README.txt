Quelo Office — pacchetti prepare-usb (release 0.71-alpha)
============================================================

Archivi per preparare la chiavetta USB dal PC host (Linux).

Pacchetto completo (CLI + GUI)
------------------------------
  Quelo_prepare_usb-0.71-alpha.zip
  Quelo_prepare_usb-0.71-alpha.rar
  Quelo_prepare_usb-0.71-alpha.tar

  Contiene: prepare-usb.sh, prepare-usb-gui.sh, prepare-usb-gui.py,
  quelo_prepare_lib.py, quelo-write-iso.py, logo.png, NOTES-MANUALE.txt

Pacchetto solo GUI
------------------
  Quelo_prepare_usb_gui-0.71-alpha.zip
  Quelo_prepare_usb_gui-0.71-alpha.rar
  Quelo_prepare_usb_gui-0.71-alpha.tar

  Contiene: prepare-usb-gui.sh, prepare-usb-gui.py, quelo_prepare_lib.py,
  quelo-write-iso.py, logo.png, NOTES-MANUALE.txt

Correzione 2026-07-13: QUELO-HOME (exFAT) con tipo partizione 0x07 per Windows.

Distribuiti su GitHub Releases insieme all'ISO:
  https://github.com/alby-quelo/quelo-office/releases/tag/0.71-alpha

Uso rapido — GUI (consigliato)
------------------------------
  1. Scarica anche Quelo_Office-0.71-alpha.iso dalla stessa pagina Release
  2. Estrai l'archivio prepare-usb-gui (o il pacchetto completo)
  3. Metti l'ISO in una cartella ISO/ accanto a SOURCE_CODE/, oppure
     selezionala dall'interfaccia grafica
  4. ./Quelo_prepare_usb_gui/prepare-usb-gui.sh

Uso rapido — CLI (terminale)
----------------------------
  1–3 come sopra
  4. sudo ./Quelo_prepare_usb/prepare-usb.sh

Prerequisiti host
-----------------
  GUI: python3, python3-tk, e2fsprogs, exfatprogs, util-linux, polkit o sudo
  CLI: e2fsprogs, exfatprogs, util-linux

Per rigenerare gli archivi (sviluppo):
  SOURCE_CODE/Quelo_prepare_usb/build-archives.sh

Vedi CHANGELOG.TXT per le novità della GUI.
