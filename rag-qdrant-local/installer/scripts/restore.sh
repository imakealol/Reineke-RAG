#!/usr/bin/env bash
# =============================================================================
# Reineke-RAG · restore from backup
# =============================================================================
# Stellt SQLite, Qdrant-Snapshot und Konfiguration aus einem backup.sh-Archiv
# wieder her. Stoppt Container während der Operation.
#
# Aufruf:
#   restore.sh /opt/reineke-rag/backups/reineke-rag-YYYYMMDD-HHMMSS.tar.gz
# =============================================================================

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/reineke-rag}"
ARCHIVE="${1:?usage: restore.sh <backup.tar.gz>}"

[[ -f "$ARCHIVE" ]] || { echo "Backup nicht gefunden: $ARCHIVE"; exit 1; }
[[ $EUID -eq 0 ]]   || { echo "Bitte als root oder mit sudo ausführen."; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
tar xzf "$ARCHIVE" -C "$work"

echo "⚠  Aktuelle Daten werden überschrieben. Weiter? (JA/nein)"
read -r ans
[[ "$ans" == "JA" ]] || { echo "abgebrochen"; exit 0; }

echo "→ Container stoppen"
( cd "$INSTALL_DIR" && docker compose down )

echo "→ SQLite zurückspielen"
cp "$work/rag.sqlite" "$INSTALL_DIR/storage/rag.sqlite"

if [[ -f "$work/qdrant.snapshot" ]]; then
  echo "→ Qdrant-Snapshot zurückspielen"
  collection="$(grep -E '^QDRANT_COLLECTION=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
  collection="${collection:-documents}"
  mkdir -p "$INSTALL_DIR/qdrant/snapshots/${collection}"
  cp "$work/qdrant.snapshot" "$INSTALL_DIR/qdrant/snapshots/${collection}/restore.snapshot"
fi

if [[ -d "$work/converted" ]]; then
  echo "→ konvertierte Office-Dateien zurückspielen"
  rm -rf "$INSTALL_DIR/storage/converted"
  cp -r "$work/converted" "$INSTALL_DIR/storage/converted"
fi

echo "→ Container starten"
( cd "$INSTALL_DIR" && docker compose up -d )

if [[ -f "$work/qdrant.snapshot" ]]; then
  echo "→ Qdrant-Snapshot importieren (kann eine Minute dauern)"
  collection="$(grep -E '^QDRANT_COLLECTION=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
  collection="${collection:-documents}"
  curl -fsS -X PUT "http://127.0.0.1:6333/collections/${collection}/snapshots/recover" \
    -H 'Content-Type: application/json' \
    -d "{\"location\":\"file:///qdrant/storage/snapshots/${collection}/restore.snapshot\"}" \
    || echo "⚠  Qdrant-Recover fehlgeschlagen — bitte manuell prüfen."
fi

echo "✓ Restore abgeschlossen"
