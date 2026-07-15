Quelo prepare-usb — sorgente GUI Windows
========================================

Codice sorgente della GUI per preparare la chiavetta su Windows (32/64 bit).

Avvio su Windows (dopo aver scaricato gli asset offline):
  AVVIA.bat

Per scaricare Python 32 bit, mke2fs, FATtools, sgdisk32 (su Linux di develop):
  ./windows/fetch-offline-assets.sh

Archivi distribuzione (zip/rar/tar):
  DOWNLOAD/Quelo-prepare_usb_windows.{zip,rar,tar}

NON contiene i binari offline (python/, tools/*.exe, installers/*.exe):
si ricostruiscono con fetch-offline-assets.sh.
