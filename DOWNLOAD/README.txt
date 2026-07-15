Quelo Office — pacchetti prepare-usb (release 0.71-alpha)
============================================================

Archivi per preparare la chiavetta USB dal PC host (Linux o Windows).

Pacchetto completo Linux (CLI + GUI)
------------------------------------
  Quelo_prepare_usb-0.71-alpha.zip
  Quelo_prepare_usb-0.71-alpha.rar
  Quelo_prepare_usb-0.71-alpha.tar

  Contiene: prepare-usb.sh, prepare-usb-gui.sh, prepare-usb-gui.py,
  quelo_prepare_lib.py, quelo-write-iso.py, logo.png, NOTES-MANUALE.txt

Pacchetto solo GUI Linux
------------------------
  Quelo_prepare_usb_gui-0.71-alpha.zip
  Quelo_prepare_usb_gui-0.71-alpha.rar
  Quelo_prepare_usb_gui-0.71-alpha.tar

  Contiene: prepare-usb-gui.sh, prepare-usb-gui.py, quelo_prepare_lib.py,
  quelo-write-iso.py, logo.png, NOTES-MANUALE.txt

Pacchetto GUI Windows (32 e 64 bit, OFFLINE)
--------------------------------------------
  Quelo-prepare_usb_windows.zip
  Quelo-prepare_usb_windows.rar
  Quelo-prepare_usb_windows.tar

  Contiene: AVVIA.bat, prepare-usb-gui.py, quelo_prepare_win_lib.py,
  quelo-write-iso.py, windows\ (Python 32 bit, mke2fs, FATtools, …),
  LEGGIMI-WINDOWS.txt / LEGGIMI.txt

Distribuiti su GitHub Releases insieme all'ISO:
  https://github.com/alby-quelo/quelo-office/releases/tag/0.71-alpha

Uso rapido — GUI Linux (consigliato su Linux)
---------------------------------------------
  1. Scarica anche Quelo_Office-0.71-alpha.iso dalla stessa pagina Release
  2. Estrai l'archivio prepare-usb-gui (o il pacchetto completo)
  3. Metti l'ISO in una cartella ISO/ accanto a SOURCE_CODE/, oppure
     selezionala dall'interfaccia grafica
  4. ./Quelo_prepare_usb_gui/prepare-usb-gui.sh

Uso rapido — CLI Linux (terminale)
----------------------------------
  1–3 come sopra
  4. sudo ./Quelo_prepare_usb/prepare-usb.sh

Uso rapido — GUI Windows (32 e 64 bit)
--------------------------------------
  1. Scarica anche Quelo_Office-0.71-alpha.iso dalla stessa pagina Release
  2. Estrai Quelo-prepare_usb_windows.zip (oppure .rar / .tar)
  3. Doppio clic su AVVIA.bat (accetta UAC amministratore)
  4. Nella GUI: scegli ISO, chiavetta USB, dimensione persistenza
  5. Conferme: numero disco + digita SI SCRIVI

  Pacchetto offline: non serve Internet.
  Problemi: tasto destro su AVVIA.bat → Esegui come amministratore,
  poi apri windows\LOG-FULL.txt (vedi anche LEGGIMI.txt nell'archivio).

Prerequisiti host
-----------------
  GUI Linux: python3, python3-tk, e2fsprogs, exfatprogs, util-linux, polkit o sudo
  CLI Linux: e2fsprogs, exfatprogs, util-linux
  GUI Windows: Windows 7 SP1 o superiore (32 o 64 bit), privilegi amministratore
               (Python e mke2fs sono inclusi nel pacchetto)

Per rigenerare gli archivi (sviluppo):
  SOURCE_CODE/Quelo_prepare_usb/build-archives.sh

Vedi CHANGELOG.TXT per le novità della GUI.
