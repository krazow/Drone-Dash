#!/usr/bin/env bash
# Install Drone Dash as a service on a Raspberry Pi (Raspberry Pi OS / Ubuntu).
# Usage:  sudo ./install.sh                       (run from this folder)
#         sudo APP_SLUG=other-name ./install.sh    (install under a different folder/service name)
# Re-running is safe: it updates the code and keeps your settings and data.
set -euo pipefail
cd "$(dirname "$0")"                   # so it works when called from rename.sh or another folder
APP_SLUG=${APP_SLUG:-drone-dash}          # folder, settings file and service name
APP_DIR=/opt/$APP_SLUG
ENV_FILE=/etc/default/$APP_SLUG
USER_NAME=${SUDO_USER:-pi}

apt-get update
apt-get install -y python3-venv
mkdir -p "$APP_DIR"
cp server.py index.html simulator.py requirements.txt "$APP_DIR"/
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R "$USER_NAME" "$APP_DIR"

if [ ! -f "$ENV_FILE" ]; then
cat > "$ENV_FILE" <<'EOF'
# Edit, then restart the service (sudo systemctl restart <slug>).
# Display name shown in the page title, header and phone alerts. Change any time.
APP_NAME="Drone Dash"
# Shared secret senders must pass as ?token=... or an X-Token header (leave empty to disable)
APP_TOKEN=
# Phone alerts: install the ntfy app and subscribe to a long random topic name
APP_NTFY_URL=
# Your node names and positions (keep the single quotes)
APP_NODES='{"waitsburg-1":[46.2704,-118.1530],"dayton-1":[46.3207,-117.9719]}'
APP_LIVE_SECS=120
APP_RETENTION_DAYS=90
EOF
fi

cat > /etc/systemd/system/$APP_SLUG.service <<EOF
[Unit]
Description=$APP_SLUG drone dashboard
After=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$APP_SLUG"
systemctl restart "$APP_SLUG"
echo "Running as service '$APP_SLUG': http://$(hostname -I | awk '{print $1}'):8080"
