#!/usr/bin/env bash
# Wartet, bis ein HTTP-Endpunkt 2xx/3xx zurückgibt (oder Timeout).
# Aufruf: wait-for.sh <url> [timeout_sec=60]
set -euo pipefail

URL="${1:?usage: wait-for.sh <url> [timeout_sec]}"
TIMEOUT="${2:-60}"
deadline=$(( $(date +%s) + TIMEOUT ))

printf "    warte auf %s " "$URL"
while :; do
  if curl -fsS --max-time 3 -o /dev/null "$URL" 2>/dev/null; then
    printf " ✓\n"
    exit 0
  fi
  if (( $(date +%s) >= deadline )); then
    printf " ✗ Timeout nach %ss\n" "$TIMEOUT"
    exit 1
  fi
  printf "."
  sleep 2
done
