#!/usr/bin/env bash
# Lokalen Server im Hintergrund starten und wieder beenden, um die App anzusehen:
#   mise run app:start   startet `mise run serve`, wartet auf /healthz, nennt Adresse und Log
#   mise run app:stop    beendet genau diesen Server (einen von Hand gestarteten lässt es stehen)
set -euo pipefail
cd "$(dirname "$0")/.."
port="${PORT:-8080}"
url="http://localhost:$port"
log="data/dev-server.log"
pidfile="data/dev-server.pid"

up() { curl -sf -o /dev/null -m 2 "$url/healthz"; }

case "${1:-}" in
  start)
    if up; then
      echo "Läuft schon: $url"
      exit 0
    fi
    mkdir -p data
    nohup mise run serve >"$log" 2>&1 &
    echo $! >"$pidfile"
    for _ in $(seq 1 40); do
      if up; then
        echo "Bereit: $url (Log: $log)"
        exit 0
      fi
      sleep 0.5
    done
    echo "Der Server startet nicht. Letzte Zeilen aus $log:" >&2
    tail -n 20 "$log" >&2
    exit 1
    ;;
  stop)
    if [ ! -f "$pidfile" ]; then
      echo "Kein von app:start gestarteter Server."
      exit 0
    fi
    kill "$(cat "$pidfile")" 2>/dev/null || true
    # uvicorn --reload hält den Port in einem Kindprozess
    lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
    rm -f "$pidfile"
    echo "Beendet."
    ;;
  *)
    echo "Aufruf: $0 start|stop" >&2
    exit 2
    ;;
esac
