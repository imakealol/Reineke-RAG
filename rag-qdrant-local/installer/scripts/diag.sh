#!/usr/bin/env bash
# Sammelt Diagnose-Infos für den Support. Schreibt nach stdout.
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/reineke-rag}"

echo "=== System ==="
uname -a
echo
echo "=== Docker ==="
docker --version
docker compose version
echo
echo "=== Stack ==="
( cd "$INSTALL_DIR" && docker compose ps ) || true
echo
echo "=== Health ==="
curl -fsS "http://127.0.0.1:8000/health"   || echo "(backend down)"
echo
curl -fsS "http://127.0.0.1:6333/readyz"   || echo "(qdrant down)"
echo
curl -fsS "http://127.0.0.1:11434/api/tags" || echo "(ollama down)"
echo
echo "=== Disk ==="
df -h "$INSTALL_DIR"
du -sh "$INSTALL_DIR"/* 2>/dev/null || true
echo
echo "=== Recent Backend Logs ==="
( cd "$INSTALL_DIR" && docker compose logs --tail=80 backend ) 2>/dev/null || true
