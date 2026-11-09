#!/usr/bin/env bash
# Legt den LXC für Tischlein auf dem Proxmox-Host an und richtet ihn ein (als root auf pve):
#   bash create-lxc.sh            # Standard: CT 104 "tischlein", DHCP, local-lvm
# Unprivilegiert mit nesting/keyctl (nötig für Docker), Debian 13, Docker aus den Debian-Paketen.
# Danach holt sich der LXC jedes Release selbst (deploy/lxc/, tischlein-update.timer).
set -euo pipefail
CTID="${CTID:-104}"
NAME="${NAME:-tischlein}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
DISK_GB="${DISK_GB:-8}"
MEMORY_MB="${MEMORY_MB:-1024}"
CORES="${CORES:-1}"
REPO="${REPO:-https://github.com/schowave/tischlein.git}"

if pct status "$CTID" >/dev/null 2>&1 || qm status "$CTID" >/dev/null 2>&1; then
  echo "ID $CTID ist schon vergeben – abgebrochen." >&2
  exit 1
fi

pveam update >/dev/null
template="$(pveam available --section system | awk '{print $2}' | grep -E '^debian-13-standard_.*_amd64\.tar\.zst$' | sort -V | tail -1)"
test -n "$template"
pveam list local | grep -q "$template" || pveam download local "$template"

pct create "$CTID" "local:vztmpl/$template" \
  --hostname "$NAME" --description "Tischlein – Familien-Essensplan (github.com/schowave/tischlein)" \
  --cores "$CORES" --memory "$MEMORY_MB" --swap 512 \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --unprivileged 1 --features nesting=1,keyctl=1 \
  --onboot 1 --timezone host --tags docker
pct start "$CTID"

# warten, bis das Netz im Container steht
for _ in $(seq 1 30); do pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done

pct exec "$CTID" -- bash -euc '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -yq docker.io docker-compose git curl ca-certificates
  docker compose version
  systemctl enable --now docker
'
pct exec "$CTID" -- bash -euc "
  rm -rf /tmp/tischlein
  git clone --depth 1 $REPO /tmp/tischlein
  bash /tmp/tischlein/deploy/lxc/install.sh
"
ip="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
echo "LXC $CTID ($NAME) läuft: http://$ip:8080"
