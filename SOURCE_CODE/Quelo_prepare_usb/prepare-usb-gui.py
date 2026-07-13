#!/usr/bin/env python3
"""GUI host per preparazione chiavetta Quelo Office. NON inclusa nell'ISO live."""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import quelo_prepare_lib as lib  # noqa: E402


class PrepareUsbGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Quelo Office — prepare-usb (PC host)")
        self.minsize(640, 580)
        self.geometry("780x700")

        self.iso_var = tk.StringVar()
        self.disk_var = tk.StringVar()
        self.persist_var = tk.IntVar(value=1024)
        self.confirm_disk_var = tk.StringVar()
        self.confirm_phrase_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Pronto.")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._disks: list[lib.DiskInfo] = []
        self._worker: threading.Thread | None = None
        self._running = False

        self._build_ui()
        self._set_window_icon()
        self._startup_checks()
        self._refresh_disks()
        self._autofill_iso()

    def _set_window_icon(self) -> None:
        for path in (
            os.path.join(SCRIPT_DIR, "logo.png"),
            os.path.join(
                SCRIPT_DIR,
                "..",
                "Quelo_office",
                "overlay",
                "usr",
                "share",
                "quelo-office",
                "logo.png",
            ),
        ):
            if os.path.isfile(path):
                try:
                    self._icon_img = tk.PhotoImage(file=path)
                    self.iconphoto(True, self._icon_img)
                except tk.TclError:
                    pass
                break

    def _build_ui(self) -> None:
        # Barra pulsanti fissa in basso (pack prima, side=BOTTOM)
        actions = ttk.Frame(self, padding=(12, 8, 12, 12))
        actions.pack(side=tk.BOTTOM, fill=tk.X)
        self.cancel_btn = ttk.Button(actions, text="Annulla", command=self.destroy)
        self.cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.start_btn = ttk.Button(actions, text="Esegui", command=self._start)
        self.start_btn.pack(side=tk.RIGHT)

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        warn = ttk.Label(
            outer,
            text=(
                "Strumento HOST — eseguire sul PC locale con la USB collegata.\n"
                "NON avviare dalla sessione live Quelo Office bootata sulla stessa chiavetta."
            ),
            foreground="#a33",
            wraplength=700,
            justify=tk.LEFT,
        )
        warn.pack(anchor=tk.W, pady=(0, 10))

        iso_frame = ttk.LabelFrame(outer, text="1. Immagine ISO", padding=10)
        iso_frame.pack(fill=tk.X, pady=4)
        row = ttk.Frame(iso_frame)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=self.iso_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(row, text="Sfoglia…", command=self._browse_iso).pack(side=tk.LEFT)

        usb_frame = ttk.LabelFrame(outer, text="2. Chiavetta USB", padding=10)
        usb_frame.pack(fill=tk.X, pady=4)
        row2 = ttk.Frame(usb_frame)
        row2.pack(fill=tk.X)
        self.disk_combo = ttk.Combobox(row2, textvariable=self.disk_var, state="readonly", width=70)
        self.disk_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(row2, text="Aggiorna", command=self._refresh_disks).pack(side=tk.LEFT)
        self.root_disk_label = ttk.Label(usb_frame, text="", foreground="#666")
        self.root_disk_label.pack(anchor=tk.W, pady=(6, 0))

        persist_frame = ttk.LabelFrame(outer, text="3. Dimensione persistenza (ext4)", padding=10)
        persist_frame.pack(fill=tk.X, pady=4)
        for mb in lib.PERSIST_SIZES_MB:
            label = f"{mb} MB"
            if mb == 1024:
                label += " (consigliato)"
            ttk.Radiobutton(
                persist_frame,
                text=label,
                variable=self.persist_var,
                value=mb,
            ).pack(anchor=tk.W)

        confirm_frame = ttk.LabelFrame(outer, text="4. Conferma sicurezza", padding=10)
        confirm_frame.pack(fill=tk.X, pady=4)
        ttk.Label(
            confirm_frame,
            text="Tutti i dati sul disco USB verranno SOVRASCRITTI.",
            foreground="#a33",
        ).pack(anchor=tk.W)
        ttk.Label(confirm_frame, text="Conferma 1/2 — nome disco (es. sdb):").pack(anchor=tk.W, pady=(6, 0))
        ttk.Entry(confirm_frame, textvariable=self.confirm_disk_var).pack(fill=tk.X)
        ttk.Label(confirm_frame, text='Conferma 2/2 — digita: SI SCRIVI').pack(anchor=tk.W, pady=(6, 0))
        ttk.Entry(confirm_frame, textvariable=self.confirm_phrase_var).pack(fill=tk.X)

        prog_frame = ttk.LabelFrame(outer, text="Avanzamento", padding=10)
        prog_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.progress = ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 8))
        self.status_label = ttk.Label(
            prog_frame,
            textvariable=self.status_var,
            anchor=tk.W,
            wraplength=680,
            padding=(0, 2),
        )
        self.status_label.pack(fill=tk.X, anchor=tk.W, pady=(0, 8))
        log_wrap = ttk.Frame(prog_frame)
        log_wrap.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_wrap)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log = tk.Text(
            log_wrap,
            height=6,
            wrap=tk.WORD,
            state=tk.DISABLED,
            yscrollcommand=scroll.set,
        )
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.configure(command=self.log.yview)

    def _startup_checks(self) -> None:
        try:
            lib.require_host()
        except lib.PrepareError as exc:
            messagebox.showerror("Sessione live", str(exc))
            self.after(100, self.destroy)
            return

        if os.geteuid() != 0:
            messagebox.showerror(
                "Permessi",
                "Serve esecuzione come root.\n"
                "Usa: ./prepare-usb-gui.sh (pkexec/sudo)",
            )
            self.after(100, self.destroy)
            return

        missing = lib.missing_dependencies()
        if missing:
            messagebox.showerror(
                "Dipendenze mancanti",
                "Installa:\n• " + "\n• ".join(missing),
            )
            self.after(100, self.destroy)
            return

        root = lib.root_disk()
        if root:
            self.root_disk_label.configure(text=f"Disco di sistema (NON usare): {root}")

    def _autofill_iso(self) -> None:
        found = lib.find_publish_iso()
        if found and not self.iso_var.get():
            self.iso_var.set(found)

    def _browse_iso(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleziona ISO Quelo Office",
            filetypes=[("ISO", "*.iso"), ("Tutti i file", "*.*")],
        )
        if path:
            self.iso_var.set(path)

    def _refresh_disks(self) -> None:
        try:
            self._disks = lib.list_disks()
        except lib.PrepareError as exc:
            messagebox.showerror("Dischi", str(exc))
            return

        labels = []
        for d in self._disks:
            tag = "USB" if d.is_usb else "ATTENZIONE: non USB"
            labels.append(f"{d.path}  {d.size}  {d.model}  [{tag}]")
        self.disk_combo["values"] = labels
        if labels:
            # prefer first USB disk
            usb_idx = next((i for i, d in enumerate(self._disks) if d.is_usb), 0)
            self.disk_combo.current(usb_idx)

    def _selected_disk(self) -> str | None:
        idx = self.disk_combo.current()
        if idx < 0 or idx >= len(self._disks):
            return None
        return self._disks[idx].path

    def _append_log(self, line: str) -> None:
        def _do() -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, line + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        self.after(0, _do)

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self.status_var.set(text))

    def _set_progress(self, pct: int, label: str) -> None:
        def _do() -> None:
            self.progress_var.set(max(0, min(100, pct)))
            self.status_var.set(label)
            self.status_label.configure(text=label)

        self.after(0, _do)

    def _start(self) -> None:
        if self._running:
            return

        iso = self.iso_var.get().strip()
        disk = self._selected_disk()
        persist_mb = self.persist_var.get()
        disk_name = self.confirm_disk_var.get().strip()
        phrase = self.confirm_phrase_var.get()

        if not iso or not os.path.isfile(iso):
            messagebox.showwarning("ISO", "Seleziona un file ISO valido.")
            return
        if not disk:
            messagebox.showwarning("USB", "Seleziona una chiavetta USB.")
            return

        info = self._disks[self.disk_combo.current()]
        try:
            lib.validate_confirm_text(info.name, disk_name, phrase)
        except lib.PrepareError as exc:
            messagebox.showwarning("Conferma", str(exc))
            return

        root = lib.root_disk()
        if root and disk == root:
            messagebox.showerror("USB", "Hai scelto il disco di sistema. STOP.")
            return

        allow_non_usb = False
        if not info.is_usb:
            if not messagebox.askyesno(
                "Attenzione",
                "Il disco non risulta USB/removable.\nContinuare comunque?",
            ):
                return
            allow_non_usb = True

        if not messagebox.askokcancel(
            "Ultima conferma",
            f"Sto per preparare:\n\n"
            f"ISO: {iso}\n"
            f"USB: {disk}\n"
            f"Persistenza: {persist_mb} MB\n\n"
            f"Tutti i dati su {disk} verranno cancellati.",
        ):
            return

        self._running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.progress_var.set(0)
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

        def worker() -> None:
            try:
                def on_progress(pct: int, _cur: int, _total: int, label: str) -> None:
                    self._set_progress(pct, label)

                lib.run_prepare(
                    iso,
                    disk,
                    persist_mb,
                    allow_non_usb=allow_non_usb,
                    log=self._append_log,
                    progress=on_progress,
                )
            except lib.PrepareError as exc:
                self._append_log(f"ERRORE: {exc}")
                self.after(0, lambda: messagebox.showerror("Errore", str(exc)))
            else:
                self._set_progress(100, "Completato.")
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Completato",
                        "Chiavetta pronta.\n\n"
                        "1. Rimuovi la USB in sicurezza\n"
                        "2. Boot da USB\n"
                        "3. Allo spegnimento salva le configurazioni che vuoi conservare",
                    ),
                )
            finally:
                def _done() -> None:
                    self._running = False
                    self.start_btn.configure(state=tk.NORMAL)
                    self.cancel_btn.configure(state=tk.NORMAL)

                self.after(0, _done)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()


def main() -> int:
    if not os.environ.get("DISPLAY"):
        print("ERRORE: DISPLAY non impostato (serve sessione grafica).", file=sys.stderr)
        return 1
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("ERRORE: python3-tk non installato.", file=sys.stderr)
        return 1

    app = PrepareUsbGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
