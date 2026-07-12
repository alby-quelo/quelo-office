# Applica locale di sistema
if [[ -f /etc/default/locale ]]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/default/locale
  set +a
fi
