Quelo Office — prepare-usb GUI per Windows 7+
==============================================

ITALIANO
--------

Interfaccia grafica per preparare la chiavetta USB Quelo Office dal PC Windows.
Stessi passi della versione Linux: pulizia, scrittura ISO, partizioni,
persistenza ext4, home exFAT.

Requisiti
---------
  • Windows 7 SP1 o superiore (64 bit consigliato)
  • Python 3.8+ con tkinter (opzione Tcl/Tk nell'installer python.org)
  • Privilegi amministratore (UAC)
  • mke2fs.exe (e2fsprogs) per formattare la partizione ext4 «persistence»
    — vedi «Strumenti e2fsprogs» sotto

Avvio
-----
  1. Estrai l'archivio Quelo_prepare_usb_gui_win-0.71-alpha.zip
  2. Esegui windows\install-dependencies.bat (prima volta)
  3. Doppio clic su windows\prepare-usb-gui.bat
  4. Conferma UAC (amministratore)

  (In alternativa: windows\setup-tools.bat solo per mke2fs, se Python è già installato.)

Conferma sicurezza
------------------
  • Conferma 1/2: digita il NUMERO del disco USB (es. 2), come mostrato
    nell'elenco «Disco 2 …»
  • Conferma 2/2: digita SI SCRIVI

Strumenti e2fsprogs (mke2fs)
----------------------------
  Windows non formatta ext4 nativamente. Il pacchetto usa mke2fs.exe:

  Metodo A — automatico (consigliato):
    Esegui windows\install-dependencies.bat
    Scarica mke2fs da mirror Cygwin (Windows 10+ con tar e Internet)

  Metodo B — Cygwin installato:
    1. Installa Cygwin64 con il pacchetto e2fsprogs
    2. Esegui windows\setup-tools.bat

  Metodo C — manuale:
    Copia mke2fs.exe e le DLL Cygwin necessarie in:
      windows\tools\e2fsprogs\

  Se mke2fs manca, la GUI mostra un errore all'avvio con queste istruzioni.

Dipendenze incluse in Windows
-----------------------------
  • diskpart, format — inclusi in Windows
  • Python 3 + tkinter — da installare
  • mke2fs.exe — da setup-tools o copia manuale

Note
----
  • Chiudi Esplora risorse sulla USB prima di avviare
  • Non usare il disco di sistema (di solito Disco 0)
  • L'ISO non è inclusa: scaricala dalla Release GitHub


ENGLISH
-------

Graphical tool to prepare a Quelo Office USB stick from Windows.
Same steps as the Linux version: wipe, ISO write, partitions,
ext4 persistence, exFAT home.

Requirements
------------
  • Windows 7 SP1 or newer (64-bit recommended)
  • Python 3.8+ with tkinter (enable Tcl/Tk in the python.org installer)
  • Administrator privileges (UAC)
  • mke2fs.exe (e2fsprogs) to format the ext4 «persistence» partition
    — see «e2fsprogs tools» below

Start
-----
  1. Extract Quelo_prepare_usb_gui_win-0.71-alpha.zip
  2. Run windows\install-dependencies.bat (first time)
  3. Double-click windows\prepare-usb-gui.bat
  4. Approve UAC (administrator)

  (Alternatively: windows\setup-tools.bat for mke2fs only, if Python is already installed.)

Safety confirmation
-------------------
  • Confirm 1/2: type the USB disk NUMBER (e.g. 2) as shown in the list
  • Confirm 2/2: type SI SCRIVI

e2fsprogs tools (mke2fs)
------------------------
  Windows cannot format ext4 natively. This package uses mke2fs.exe:

  Method A — automatic (recommended):
    Run windows\install-dependencies.bat
    Downloads mke2fs from Cygwin mirror (Windows 10+ with tar and Internet)

  Method B — Cygwin installed:
    1. Install Cygwin64 with the e2fsprogs package
    2. Run windows\setup-tools.bat

  Method C — manual:
    Copy mke2fs.exe and required Cygwin DLLs to:
      windows\tools\e2fsprogs\

  If mke2fs is missing, the GUI shows an error at startup with these steps.

Dependencies on Windows
-----------------------
  • diskpart, format — built into Windows
  • Python 3 + tkinter — install separately
  • mke2fs.exe — via setup-tools or manual copy

Notes
-----
  • Close File Explorer on the USB stick before starting
  • Do not use the system disk (usually Disk 0)
  • ISO not included: download from the GitHub Release
