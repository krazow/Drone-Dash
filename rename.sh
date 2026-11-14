#!/usr/bin/env bash
# Move an installed copy to a new folder/service name, keeping settings and data.
# Usage: sudo ./rename.sh drone-dash touchet-skywatch "Touchet Skywatch"
#   arg 1: current slug   arg 2: new slug   arg 3 (optional): new display name
# Only needed if you want the folder and service renamed too; for the name on screen,
# just edit APP_NAME in /etc/default/<slug> and restart.
set -euo pipefail
OLD=$1; NEW=$2; NAME=${3:-}
[[ "$NEW" =~ ^[a-z0-9-]+$ ]] || { echo "new slug must be lowercase letters, digits and dashes"; exit 1; }
[ -d "/opt/$OLD" ] || { echo "/opt/$OLD not found"; exit 1; }
[ -e "/opt/$NEW" ] && { echo "/opt/$NEW already exists"; exit 1; }

systemctl stop "$OLD" || true
systemctl disable "$OLD" || true
mv "/opt/$OLD" "/opt/$NEW"
mv "/etc/default/$OLD" "/etc/default/$NEW"
rm -f "/etc/systemd/system/$OLD.service"
if [ -n "$NAME" ]; then
  sed -i "s|^APP_NAME=.*|APP_NAME=\"$NAME\"|" "/etc/default/$NEW"
fi
rm -rf "/opt/$NEW/venv"     # venvs hard-code their path; install.sh rebuilds it
APP_SLUG=$NEW "$(dirname "$0")/install.sh"
