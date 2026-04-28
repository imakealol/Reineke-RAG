#!/usr/bin/env bash
# =============================================================================
# Reineke-RAG · nightly backup
# =============================================================================
# Erstellt einen konsistenten Snapshot von:
#   - SQLite-Metadaten   (storage/rag.sqlite via .backup-API)
#   - Qdrant-Collection  (snapshot via REST)
#   - Ollama-Konfiguration (Modell-Manifeste, NICHT die Modell-Blobs)
#   - .env, docker-compose.yml
#
# Aufruf:
#   backup.sh                  # legt /opt/reineke-rag/backups/<datum>.tar.gz an
#   BACKUP_DIR=/mnt/nas backup.sh
# =============================================================================

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/reineke-rag}"
BACKUP_DIR="${BACKUP_DIR:-$INSTALL_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

ts="$(date +'%Y%m%d-%H%M%S')"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$BACKUP_DIR"

echo "→ SQLite konsistent kopieren"
docker compose -f "$INSTALL_DIR/docker-compose.yml" exec -T backend \
  sqlite3 /app/storage/rag.sqlite ".backup '/app/storage/rag.backup'"
cp "$INSTALL_DIR/storage/rag.backup" "$work/rag.sqlite"
rm -f "$INSTALL_DIR/storage/rag.backup"

echo "→ Qdrant-Snapshot anstoßen"
collection="$(grep -E '^QDRANT_COLLECTION=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
collection="${collection:-documents}"
snap_resp="$(curl -fsS -X POST "http://127.0.0.1:6333/collections/${collection}/snapshots" || echo '{}')"
snap_name="$(echo "$snap_resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("result",{}).get("name",""))' || true)"
if [[ -n "$snap_name" ]]; then
  cp "$INSTALL_DIR/qdrant/snapshots/${collection}/${snap_name}" "$work/qdrant.snapshot" 2>/dev/null || true
fi

echo "→ Konfiguration sichern"
cp "$INSTALL_DIR/.env"               "$work/.env"
cp "$INSTALL_DIR/docker-compose.yml" "$work/docker-compose.yml"

echo "→ konvertierte Office-Dateien sichern (klein)"
[[ -d "$INSTALL_DIR/storage/converted" ]] && cp -r "$INSTALL_DIR/storage/converted" "$work/" || true

out="$BACKUP_DIR/reineke-rag-${ts}.tar.gz"
tar czf "$out" -C "$work" .
chmod 0640 "$out"
echo "→ Backup geschrieben: $out  ($(du -h "$out" | cut -f1))"

echo "→ alte Backups (>${RETENTION_DAYS} Tage) löschen"
find "$BACKUP_DIR" -name 'reineke-rag-*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "✓ Backup fertig"
