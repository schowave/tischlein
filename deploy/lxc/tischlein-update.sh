#!/usr/bin/env bash
# Holt das neueste Release-Image und startet Tischlein nur neu, wenn es sich geändert hat.
# Kommt die neue Version nicht gesund hoch, läuft wieder die vorherige. Läuft per systemd-Timer.
set -euo pipefail
cd /opt/tischlein
image="ghcr.io/schowave/tischlein:latest"
log() { echo "tischlein-update: $*"; }

before="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
docker compose pull --quiet
after="$(docker image inspect --format '{{.Id}}' "$image")"
if [ "$before" = "$after" ] && docker compose ps --status running --quiet | grep -q .; then
  exit 0  # nichts Neues, Container läuft
fi

version="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")"
log "neue Version $version wird gestartet"
docker compose up -d --remove-orphans

for _ in $(seq 1 30); do
  if curl -sf http://localhost:8080/healthz | grep -q "\"version\":\"$version\""; then
    log "Version $version läuft"
    docker image prune -f >/dev/null
    if [ -n "${HA_WEBHOOK_URL:-}" ]; then
      curl -sf -X POST -H 'Content-Type: application/json' \
        -d "{\"titel\":\"Tischlein\",\"nachricht\":\"Version $version ist installiert\"}" "$HA_WEBHOOK_URL" >/dev/null || true
    fi
    exit 0
  fi
  sleep 2
done

log "Version $version startet nicht – zurück auf die vorherige"
if [ -n "$before" ]; then
  docker tag "$before" "$image"
  docker compose up -d --remove-orphans
fi
exit 1
