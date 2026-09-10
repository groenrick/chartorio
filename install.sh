#!/bin/bash
# Installs Chartorio next to an existing Factorio headless server.
#
# It copies the bridge and web assets to /opt/chartorio, creates a service
# account, generates an RCON password, patches your save with the scenario
# script, and installs a systemd unit. It does not edit your Factorio unit:
# the RCON flags it needs are printed at the end for you to add.
set -euo pipefail

FACTORIO_DIR="${FACTORIO_DIR:-/opt/factorio}"
SAVE="${SAVE:-$FACTORIO_DIR/saves/world.zip}"
INSTALL_DIR="${INSTALL_DIR:-/opt/chartorio}"
RCON_PORT="${RCON_PORT:-27015}"
PORT="${PORT:-8080}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "run this as root" >&2
  exit 1
fi
if [ ! -f "$SAVE" ]; then
  echo "save not found: $SAVE" >&2
  echo "set SAVE=/path/to/your.zip, or create one with: factorio --create $SAVE" >&2
  exit 1
fi
if ! command -v python3 >/dev/null; then
  echo "python3 is required (3.9 or newer)" >&2
  exit 1
fi
if systemctl is-active --quiet factorio; then
  echo "stop the Factorio service first: a running server would overwrite the patched save" >&2
  exit 1
fi

id chartorio >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin chartorio

install -d -o root -g chartorio -m 750 "$INSTALL_DIR" "$INSTALL_DIR/bridge" "$INSTALL_DIR/web"
install -o root -g chartorio -m 640 "$SOURCE_DIR/bridge/bridge.py" "$INSTALL_DIR/bridge/bridge.py"
install -o root -g chartorio -m 640 "$SOURCE_DIR/web/index.html" "$INSTALL_DIR/web/index.html"

install -d -m 750 /etc/chartorio
if [ ! -f /etc/chartorio/rcon.env ]; then
  printf 'RCON_PASSWORD=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)" \
    > /etc/chartorio/rcon.env
fi
chgrp chartorio /etc/chartorio/rcon.env
chmod 640 /etc/chartorio/rcon.env

python3 "$SOURCE_DIR/tools/patch-save.py" "$SAVE" "$SOURCE_DIR/scenario/control.lua"
chown "$(stat -c '%U:%G' "$FACTORIO_DIR/saves")" "$SAVE" 2>/dev/null || true

sed -e "s|CHARTORIO_PORT=8080|CHARTORIO_PORT=$PORT|" \
    -e "s|/opt/chartorio|$INSTALL_DIR|g" \
    "$SOURCE_DIR/systemd/chartorio.service" > /etc/systemd/system/chartorio.service
systemctl daemon-reload
systemctl enable chartorio

cat <<NEXT

Chartorio is installed. Two things left, both on the Factorio side:

1. Add these flags to your Factorio server command line so the bridge can talk
   to it (the password is in /etc/chartorio/rcon.env):

     --rcon-bind 127.0.0.1:$RCON_PORT --rcon-password "\$RCON_PASSWORD"

   With systemd, add to the [Service] section of your factorio unit:

     EnvironmentFile=/etc/chartorio/rcon.env

2. Start both services:

     systemctl start factorio
     systemctl start chartorio

Then open http://<this-host>:$PORT

NEXT
