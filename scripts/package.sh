#!/usr/bin/env bash
# Packages Drone Dash for the release kit (github.com/krazow/release-kit), once per channel.
# The kit sets CHANNEL (beta|prod), VERSION, LABEL (e.g. 1.1.0-beta.2) and OUT.
# Result: DroneDash-<label>.zip, holding a drone-dash/ folder ready for: sudo ./install.sh (or update.sh).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$(mktemp -d)/drone-dash
mkdir -p "$W"
cp "$ROOT"/{server.py,index.html,simulator.py,requirements.txt,install.sh,rename.sh,update.sh,README.md} "$W"/
sed -i "s/^APP_VERSION = \"[^\"]*\"/APP_VERSION = \"$LABEL\"/" "$W/server.py"
grep -q "^APP_VERSION = \"$LABEL\"" "$W/server.py"
chmod +x "$W"/*.sh
(cd "$(dirname "$W")" && zip -qr "$OUT/DroneDash-$LABEL.zip" drone-dash)
echo "Packaged DroneDash-$LABEL.zip"
