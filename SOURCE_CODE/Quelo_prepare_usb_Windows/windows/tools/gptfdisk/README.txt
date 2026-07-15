GPT fdisk (sgdisk32) — opzionale / diagnostica
==============================================

NOTA: le ISO ibride Quelo hanno MBR+GPT non standard. sgdisk risponde
«Invalid partition data!» — è normale. La GUI usa append GPT raw in Python
(equivalente a sfdisk --append su Linux), non sgdisk.

File: sgdisk32.exe (32 bit, opzionale)
