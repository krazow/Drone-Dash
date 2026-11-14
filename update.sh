#!/usr/bin/env bash
# Update Drone Dash on the Pi from GitHub releases. Keeps your settings and data.
# Usage:  sudo ./update.sh          the latest production release, as service 'drone-dash' on port 8080
#         sudo ./update.sh beta     the newest beta, as a separate service 'drone-dash-beta' on port 8081,
#                                   with its own settings and data, so the real one keeps running
set -euo pipefail
REPO=krazow/Drone-Dash
CHANNEL=${1:-prod}
command -v curl >/dev/null && command -v unzip >/dev/null || { apt-get update; apt-get install -y curl unzip; }
if [ "$CHANNEL" = beta ]; then
  URL=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=20" | python3 -c '
import json, sys
rel = [r for r in json.load(sys.stdin) if r["prerelease"] and not r["draft"]]
assets = [a["browser_download_url"] for a in (rel[0]["assets"] if rel else []) if a["name"].endswith(".zip")]
print(assets[0] if assets else "")')
  export APP_SLUG=${APP_SLUG:-drone-dash-beta} APP_PORT=${APP_PORT:-8081}
else
  URL=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | python3 -c '
import json, sys
print(next((a["browser_download_url"] for a in json.load(sys.stdin).get("assets", []) if a["name"].endswith(".zip")), ""))')
fi
[ -n "$URL" ] || { echo "No $CHANNEL release with a .zip found on github.com/$REPO/releases"; exit 1; }
T=$(mktemp -d)
echo "Downloading $URL"
curl -fsSL "$URL" -o "$T/dd.zip"
unzip -q "$T/dd.zip" -d "$T"
"$T/drone-dash/install.sh"
rm -rf "$T"
