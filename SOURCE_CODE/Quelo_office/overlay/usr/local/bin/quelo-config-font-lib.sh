#!/bin/bash
# Libreria: caratteri menu Openbox e pannello LXQt inferiore (non il pannello alto).
[[ -n "${QUELO_CONFIG_FONT_LIB_LOADED:-}" ]] && return 0
QUELO_CONFIG_FONT_LIB_LOADED=1

QUELO_FONT_DEFAULT_SIZE=13
QUELO_FONT_PANELS_INI="${HOME:-/root}/.config/quelo/font-panels.ini"
QUELO_FONT_PANEL_CONF="${HOME:-/root}/.config/lxqt/panel.conf"
QUELO_FONT_PANEL_CONF_SYSTEM="/etc/xdg/lxqt/panel.conf"
QUELO_FONT_PANEL_QSS_STORE="${HOME:-/root}/.config/quelo/lxqt-panel-user.qss"
QUELO_FONT_PANEL_QSS_LIVE="${HOME:-/root}/.local/share/lxqt/themes/quelo/lxqt-panel.qss"
QUELO_FONT_PANEL_QSS_SYSTEM="/usr/share/lxqt/themes/quelo/lxqt-panel.qss"
QUELO_OPENBOX_RC="${HOME:-/root}/.config/openbox/rc.xml"

# Solo pannello inferiore: plugin customcommand + taskbar (QSS).
QUELO_FONT_PANEL_BOTTOM_PLUGINS=(menu power)

quelo_font_read_menu_size() {
  python3 - "${QUELO_OPENBOX_RC}" "${QUELO_FONT_DEFAULT_SIZE}" <<'PY'
import re
import sys

path, default = sys.argv[1], int(sys.argv[2])
try:
    with open(path, encoding="utf-8") as f:
        xml = f.read()
except OSError:
    print(default)
    raise SystemExit
for place in ("MenuItem", "MenuHeader", "MenuTitle", "Menu"):
    m = re.search(rf'<font place="{place}">.*?<size>(\d+)</size>', xml, re.S)
    if m:
        print(m.group(1))
        raise SystemExit
print(default)
PY
}

quelo_font_write_menu_size() {
  local size="$1"
  python3 - "${QUELO_OPENBOX_RC}" "${size}" <<'PY'
import re
import sys

path, size = sys.argv[1], sys.argv[2]
places = ["MenuItem", "MenuHeader", "MenuTitle"]
with open(path, encoding="utf-8") as f:
    xml = f.read()

def font_block(place: str) -> str:
    return (
        f'  <font place="{place}">\n'
        f'    <name>sans</name>\n'
        f'    <size>{size}</size>\n'
        f'    <weight>normal</weight>\n'
        f'    <slant>normal</slant>\n'
        f'  </font>'
    )

for place in places:
    pat = rf'<font place="{place}">.*?</font>'
    block = font_block(place)
    if re.search(pat, xml, re.S):
        xml = re.sub(pat, block, xml, count=1, flags=re.S)
    else:
        xml = re.sub(r'(</theme>)', block + r'\n\1', xml, count=1)

with open(path, "w", encoding="utf-8") as f:
    f.write(xml)
PY
}

quelo_font_ensure_panel_conf() {
  if [[ -f "${QUELO_FONT_PANEL_CONF}" ]]; then
    return 0
  fi
  [[ -f "${QUELO_FONT_PANEL_CONF_SYSTEM}" ]] || return 1
  mkdir -p "$(dirname "${QUELO_FONT_PANEL_CONF}")"
  cp -a "${QUELO_FONT_PANEL_CONF_SYSTEM}" "${QUELO_FONT_PANEL_CONF}"
}

quelo_font_qt_font_string() {
  local size="$1"
  python3 - "${size}" <<'PY'
import sys
print(f"sans serif,{int(float(sys.argv[1]))},-1,5,50,0,0,0,0,0")
PY
}

quelo_font_parse_panel_conf_size() {
  local section="$1" path="$2"
  python3 - "${section}" "${path}" <<'PY'
import sys

section, path = sys.argv[1], sys.argv[2]
header = f"[{section}]"
in_section = False
try:
    lines = open(path, encoding="utf-8")
except OSError:
    raise SystemExit
for raw in lines:
    line = raw.strip()
    if line == header:
        in_section = True
        continue
    if in_section and line.startswith("[") and line.endswith("]"):
        break
    if in_section and line.startswith("font="):
        val = line.split("=", 1)[1].strip().strip('"')
        parts = val.split(",")
        if len(parts) > 1:
            try:
                print(int(float(parts[1])))
            except ValueError:
                pass
        break
PY
}

quelo_font_read_panel_size() {
  if [[ -f "${QUELO_FONT_PANELS_INI}" ]]; then
    local saved
    saved="$(awk -F= '$1=="bottom" {gsub(/ /,"",$2); print $2; exit}' "${QUELO_FONT_PANELS_INI}")"
    [[ -n "${saved}" ]] && { echo "${saved}"; return 0; }
  fi

  quelo_font_ensure_panel_conf || true
  local conf="${QUELO_FONT_PANEL_CONF}"
  [[ -f "${conf}" ]] || conf="${QUELO_FONT_PANEL_CONF_SYSTEM}"
  if [[ -f "${conf}" ]]; then
    local got
    got="$(quelo_font_parse_panel_conf_size "menu" "${conf}")"
    [[ -n "${got}" ]] && { echo "${got}"; return 0; }
  fi

  echo "${QUELO_FONT_DEFAULT_SIZE}"
}

quelo_font_save_panel_ini_value() {
  local value="$1"
  mkdir -p "$(dirname "${QUELO_FONT_PANELS_INI}")"
  if [[ -f "${QUELO_FONT_PANELS_INI}" ]]; then
    awk -v v="${value}" '
      BEGIN { done=0 }
      $0 ~ "^bottom=" { print "bottom=" v; done=1; next }
      { print }
      END { if (!done) print "bottom=" v }
    ' "${QUELO_FONT_PANELS_INI}" >"${QUELO_FONT_PANELS_INI}.tmp" && \
      mv "${QUELO_FONT_PANELS_INI}.tmp" "${QUELO_FONT_PANELS_INI}"
  else
    printf 'bottom=%s\n' "${value}" >"${QUELO_FONT_PANELS_INI}"
  fi
}

quelo_font_apply_panel_conf() {
  local bottom
  bottom="$(quelo_font_read_panel_size)"

  quelo_font_ensure_panel_conf || return 1

  local bottom_font
  bottom_font="$(quelo_font_qt_font_string "${bottom}")"

  python3 - "${QUELO_FONT_PANEL_CONF}" "${bottom_font}" <<'PY'
import sys

path, bottom_font = sys.argv[1], sys.argv[2]
bottom_plugins = ["menu", "power"]

with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines()

def patch_section(name: str, font_value: str, content: list[str]) -> list[str]:
    header = f"[{name}]"
    out: list[str] = []
    in_section = False
    font_done = False
    i = 0
    while i < len(content):
        line = content[i]
        stripped = line.strip()
        if stripped == header:
            in_section = True
            font_done = False
            out.append(line)
            i += 1
            continue
        if in_section and stripped.startswith("[") and stripped.endswith("]"):
            if not font_done:
                out.append(f'font="{font_value}"')
            in_section = False
            font_done = False
        if in_section and stripped.startswith("font="):
            out.append(f'font="{font_value}"')
            font_done = True
            i += 1
            continue
        out.append(line)
        i += 1
    if in_section and not font_done:
        out.append(f'font="{font_value}"')
    return out

for plugin in bottom_plugins:
    lines = patch_section(plugin, bottom_font, lines)

text = "\n".join(lines)
if not text.endswith("\n"):
    text += "\n"
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PY
}

quelo_font_apply_taskbar_qss() {
  local bottom
  bottom="$(quelo_font_read_panel_size)"

  mkdir -p "$(dirname "${QUELO_FONT_PANEL_QSS_STORE}")" \
           "$(dirname "${QUELO_FONT_PANEL_QSS_LIVE}")"

  python3 - "${bottom}" "${QUELO_FONT_PANEL_QSS_STORE}" "${QUELO_FONT_PANEL_QSS_SYSTEM}" <<'PY'
import sys

bottom, out_path, system_path = sys.argv[1], sys.argv[2], sys.argv[3]
marker_begin = "/* quelo-config-font begin */"
marker_end = "/* quelo-config-font end */"

try:
    with open(system_path, encoding="utf-8") as f:
        base = f.read()
except OSError:
    base = ""

if marker_begin in base:
    before = base.split(marker_begin, 1)[0]
    after = base.split(marker_end, 1)[-1] if marker_end in base else ""
    base = before + after

addon = f"""{marker_begin}
#TaskBar QToolButton {{
  font-size: {int(float(bottom))}pt;
}}
{marker_end}
"""

css = base.rstrip() + "\n\n" + addon
with open(out_path, "w", encoding="utf-8") as f:
    f.write(css)
PY

  cp -a "${QUELO_FONT_PANEL_QSS_STORE}" "${QUELO_FONT_PANEL_QSS_LIVE}"
}

quelo_font_apply_panel_settings() {
  quelo_font_apply_panel_conf
  quelo_font_apply_taskbar_qss
}

quelo_font_write_panel_size() {
  local size="$1"
  quelo_font_save_panel_ini_value "${size}"
  quelo_font_apply_panel_settings
}

quelo_font_restart_panel() {
  pkill -x lxqt-panel 2>/dev/null || true
  sleep 1
  export DISPLAY="${DISPLAY:-:0}"
  export XAUTHORITY="${XAUTHORITY:-/root/.Xauthority}"
  lxqt-panel &
  sleep 1
}

quelo_font_reconfigure_openbox() {
  openbox --reconfigure 2>/dev/null || true
}

quelo_font_apply_at_session_start() {
  [[ -f "${QUELO_FONT_PANELS_INI}" || -f "${QUELO_FONT_PANEL_CONF}" ]] || return 0
  quelo_font_apply_panel_settings
  if pgrep -x lxqt-panel >/dev/null 2>&1; then
    quelo_font_restart_panel
  fi
}

quelo_font_ask_size() {
  local title="$1" current="$2"
  zenity --scale \
    --title="${title}" \
    --text="Dimensione carattere (punti):" \
    --value="${current}" \
    --min-value=8 \
    --max-value=22 \
    --step=1 \
    --width=360 2>/dev/null
}
