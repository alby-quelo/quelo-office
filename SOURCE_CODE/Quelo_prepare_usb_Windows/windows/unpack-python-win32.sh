#!/bin/bash
# Costruisce windows/python/ (32 bit, con tkinter) senza eseguire l'installer su Windows.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYDIR="${SCRIPT_DIR}/python"
EMBED_URL="https://www.python.org/ftp/python/3.9.13/python-3.9.13-embed-win32.zip"
TCLTK_URL="https://www.python.org/ftp/python/3.9.13/win32/tcltk.msi"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

if [[ -f "${PYDIR}/Lib/tkinter/__init__.py" ]] && [[ -f "${PYDIR}/tcl/tcl8.6/init.tcl" ]] \
  && [[ -f "${PYDIR}/DLLs/_tkinter.pyd" ]] && grep -qx 'DLLs' "${PYDIR}/python39._pth" 2>/dev/null; then
  echo "OK: ${PYDIR} (tkinter completo)"
  exit 0
fi

command -v 7z >/dev/null || { echo "ERRORE: serve 7z" >&2; exit 1; }
command -v unzip >/dev/null || { echo "ERRORE: serve unzip" >&2; exit 1; }

echo "Scarico Python embed win32..."
curl -fsSL -o "${TMP}/embed.zip" "${EMBED_URL}"
rm -rf "${PYDIR}"
mkdir -p "${PYDIR}/DLLs" "${PYDIR}/Lib/tkinter"

unzip -q "${TMP}/embed.zip" -d "${PYDIR}"

echo "Scarico tcltk.msi..."
curl -fsSL -o "${TMP}/tcltk.msi" "${TCLTK_URL}"
7z x -y -o"${TMP}/msi" "${TMP}/tcltk.msi" >/dev/null
7z x -y -o"${TMP}/flat" "${TMP}/msi/cab1.cab" >/dev/null

echo "Organizzo tkinter + tcl/tk..."
python3 - "${TMP}/flat" "${PYDIR}" <<'PY'
import pathlib
import shutil
import sys

flat = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])


def tkinter_py(flat_name: str) -> pathlib.Path | None:
    """Lib_tkinter_*.py -> Lib/tkinter/....py"""
    if not flat_name.startswith("Lib_tkinter_") or not flat_name.endswith(".py"):
        return None
    rest = flat_name[12:-3]
    if rest == "__init__":
        return dest / "Lib/tkinter/__init__.py"
    if rest == "__main__":
        return dest / "Lib/tkinter/__main__.py"
    if rest.startswith("test"):
        return None
    if "_" in rest:
        return None
    return dest / "Lib/tkinter" / f"{rest}.py"


for src in flat.iterdir():
    n = src.name
    if n == "_tkinter.pyd":
        shutil.copy2(src, dest / "DLLs" / n)
        shutil.copy2(src, dest / n)
    elif n in ("tcl86t.dll", "tk86t.dll"):
        shutil.copy2(src, dest / n)
    elif n.startswith("Lib_tkinter"):
        dst = tkinter_py(n)
        if dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    elif n.startswith("tcl_tcl8.6_"):
        sub = n[len("tcl_tcl8.6_") :].replace("_", "/")
        dst = dest / "tcl" / "tcl8.6" / sub
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    elif n.startswith("tk_tk8.6_"):
        sub = n[len("tk_tk8.6_") :].replace("_", "/")
        dst = dest / "tcl" / "tk8.6" / sub
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    elif n.startswith("tcl_") and not n.startswith("tcl_tcl8.6_"):
        rest = n[4:].replace("_", "/")
        dst = dest / "tcl" / rest
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

required = [
    dest / "Lib/tkinter/__init__.py",
    dest / "Lib/tkinter/filedialog.py",
    dest / "Lib/tkinter/messagebox.py",
    dest / "Lib/tkinter/ttk.py",
    dest / "tcl/tcl8.6/init.tcl",
    dest / "tcl/tk8.6/tk.tcl",
    dest / "DLLs/_tkinter.pyd",
    dest / "_tkinter.pyd",
]
missing = [str(p.relative_to(dest)) for p in required if not p.is_file()]
if missing:
    print("ERRORE: file tkinter mancanti:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)

(dest / "python39._pth").write_text(
    "python39.zip\n.\nLib\nDLLs\nimport site\n",
    encoding="ascii",
)
PY

echo "OK: Python portatile in ${PYDIR}"
ls -la "${PYDIR}/Lib/tkinter/__init__.py" "${PYDIR}/Lib/tkinter/filedialog.py"
