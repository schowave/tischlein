#!/usr/bin/env bash
# Einmalige Einrichtung im LXC (Debian mit Docker, z. B. community-scripts "docker").
# Aufruf im LXC als root:  bash install.sh
# Danach übernimmt tischlein-update.timer alle Updates.
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
install -d -m 755 /opt/tischlein
install -d -m 750 -o 10001 -g 10001 /opt/tischlein/data
install -m 644 "$src/compose.yaml" /opt/tischlein/compose.yaml
install -m 755 "$src/tischlein-update.sh" /opt/tischlein/tischlein-update.sh
install -m 644 "$src/tischlein-update.service" "$src/tischlein-update.timer" /etc/systemd/system/
command -v curl >/dev/null || { apt-get update && apt-get install -y curl; }
systemctl daemon-reload
systemctl enable --now tischlein-update.timer
systemctl start tischlein-update.service
systemctl --no-pager status tischlein-update.service | tail -5 || true  # oneshot meldet danach "inactive" (Code 3)
