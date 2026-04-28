#!/usr/bin/env bash
# =============================================================================
# Reineke-RAG · Uninstaller
# =============================================================================
# Stoppt die Container und entfernt systemd-Units.
# Daten unter /opt/reineke-rag/{storage,qdrant,ollama,backups} bleiben erhalten,
# außer --purge wird gesetzt.
# =============================================================================

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/reineke-rag}"
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      echo "Usage: $0 [--purge]"
      echo "  --purge  Löscht ZUSÄTZLICH alle Daten unter $INSTALL_DIR (irreversibel)."
      exit 0
      ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Bitte als root oder mit sudo ausführen."; exit 1; }

echo "→ stoppe systemd-Units"
systemctl disable --now reineke-rag.service          2>/dev/null || true
systemctl disable --now reineke-rag-backup.timer     2>/dev/null || true
rm -f /etc/systemd/system/reineke-rag.service \
      /etc/systemd/system/reineke-rag-backup.service \
      /etc/systemd/system/reineke-rag-backup.timer
systemctl daemon-reload

echo "→ stoppe Container"
if [[ -f "$INSTALL_DIR/docker-compose.yml" ]]; then
  ( cd "$INSTALL_DIR" && docker compose down ) || true
fi

if [[ $PURGE -eq 1 ]]; then
  echo "→ --purge: lösche $INSTALL_DIR (irreversibel!)"
  read -r -p "  Wirklich löschen? Tippe 'JA' zum Bestätigen: " ans
  if [[ "$ans" == "JA" ]]; then
    rm -rf "$INSTALL_DIR"
    userdel reineke-rag 2>/dev/null || true
    echo "  ✓ entfernt"
  else
    echo "  abgebrochen — Daten bleiben erhalten"
  fi
else
  echo "→ Daten bleiben unter $INSTALL_DIR liegen (nutze --purge zum vollständigen Entfernen)"
fi

echo "✓ Reineke-RAG entfernt"
