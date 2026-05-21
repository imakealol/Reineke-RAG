#!/usr/bin/env bash
# =============================================================================
# Reineke-RAG · Installer
# =============================================================================
# Aufruf:
#   sudo ./install.sh                  # normaler Lauf
#   sudo ./install.sh --skip-ollama    # Ollama läuft nativ auf dem Host
#   sudo ./install.sh --dry-run        # nur Voraussetzungen prüfen, nichts ändern
#
# Idempotent: ein erneuter Lauf installiert ein Update ohne Datenverlust.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Konstanten / Pfade
# ---------------------------------------------------------------------------
INSTALL_DIR="${INSTALL_DIR:-/opt/reineke-rag}"
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/var/log/reineke-rag-install.log"
SERVICE_USER="reineke-rag"
SERVICE_UID="10001"

SKIP_OLLAMA=0
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --skip-ollama) SKIP_OLLAMA=1 ;;
    --dry-run)     DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unbekanntes Argument: $arg"; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
log()  { echo "[$(date +'%H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }
ok()   { log "  ✓ $*"; }
fail() { log "  ✗ $*"; exit 1; }
step() { log ""; log "── $* ──"; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    log "    DRY-RUN: $*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# 1) Voraussetzungen prüfen
# ---------------------------------------------------------------------------
step "1/8 · Voraussetzungen prüfen"

[[ $EUID -eq 0 ]] || fail "Bitte als root oder mit sudo ausführen."

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"
chmod 640 "$LOG_FILE"

command -v docker >/dev/null \
  || fail "Docker ist nicht installiert. Siehe README §1."
docker compose version >/dev/null 2>&1 \
  || fail "Docker Compose v2 fehlt. Bitte 'docker-compose-plugin' installieren."
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',') und Compose v2 vorhanden"

[[ -f "$BUNDLE_DIR/.env" ]] \
  || fail "$BUNDLE_DIR/.env fehlt. Bitte zuerst .env.example kopieren und anpassen (siehe README §2)."
ok ".env gefunden"

# Pflichtfeld ALLOWED_BASE_PATHS lesen
ALLOWED_BASE_PATHS="$(grep -E '^ALLOWED_BASE_PATHS=' "$BUNDLE_DIR/.env" | head -1 | cut -d= -f2-)"
[[ -n "$ALLOWED_BASE_PATHS" ]] \
  || fail "ALLOWED_BASE_PATHS in .env ist leer."

# Existieren die angegebenen Pfade auf dem Host?
IFS=',' read -ra _paths <<<"$ALLOWED_BASE_PATHS"
for p in "${_paths[@]}"; do
  p="$(echo "$p" | xargs)"  # trim
  [[ -d "$p" ]] || fail "ALLOWED_BASE_PATHS enthält '$p' — Verzeichnis existiert nicht auf dem Host."
done
ok "ALLOWED_BASE_PATHS = $ALLOWED_BASE_PATHS  (alle Pfade vorhanden)"

# Disk-Space (mind. 50 GB frei unter $INSTALL_DIR-Volume)
free_gb=$(df -BG --output=avail "$(dirname "$INSTALL_DIR")" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)
[[ "$free_gb" -ge 50 ]] || log "⚠  Nur ${free_gb} GB frei — empfohlen sind 200 GB."

# GPU-Check (nur Hinweis, kein Abbruch)
if grep -qE '^USE_GPU=1' "$BUNDLE_DIR/.env"; then
  if ! command -v nvidia-smi >/dev/null; then
    log "⚠  USE_GPU=1 gesetzt, aber nvidia-smi nicht gefunden. Modelle laufen sonst auf der CPU."
  else
    ok "GPU erkannt: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
  fi
fi

[[ $DRY_RUN -eq 1 ]] && { log ""; log "Dry-run beendet — keine Änderungen vorgenommen."; exit 0; }

# ---------------------------------------------------------------------------
# 2) Verzeichnisse anlegen
# ---------------------------------------------------------------------------
step "2/8 · Verzeichnisse unter $INSTALL_DIR anlegen"

# Service-User für Volumes (UID 10001 entspricht dem Backend-User im Container)
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  run useradd --system --no-create-home --shell /usr/sbin/nologin \
              --uid "$SERVICE_UID" "$SERVICE_USER" 2>/dev/null \
    || run useradd --system --no-create-home --shell /usr/sbin/nologin \
                   "$SERVICE_USER"
  ok "Service-User '$SERVICE_USER' angelegt"
fi

run install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$INSTALL_DIR"/{storage,qdrant,ollama,backups,scripts}
run install -d -m 750 "$INSTALL_DIR"

# .env und compose nach /opt/reineke-rag/ kopieren
run cp "$BUNDLE_DIR/.env"               "$INSTALL_DIR/.env"
run cp "$BUNDLE_DIR/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
run cp "$BUNDLE_DIR/scripts/"*.sh        "$INSTALL_DIR/scripts/"
run chmod 0640 "$INSTALL_DIR/.env"
run chmod 0750 "$INSTALL_DIR/scripts/"*.sh
ok "Layout erstellt"

# ---------------------------------------------------------------------------
# 3) ALLOWED_BASE_PATHS in compose-Datei einsetzen
# ---------------------------------------------------------------------------
step "3/8 · Dokumenten-Mounts in docker-compose.yml einsetzen"

mounts=""
for p in "${_paths[@]}"; do
  p="$(echo "$p" | xargs)"
  mounts+="      - ${p}:${p}:ro"$'\n'
done

# Block zwischen den Markern ersetzen — mounts via Env an Python übergeben,
# damit Mehrzeiler nicht durch Shell-Expansion zerlegt werden.
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml" RAG_MOUNTS="$mounts" \
python3 - <<'PY'
import os
from pathlib import Path
f = Path(os.environ["COMPOSE_FILE"])
mounts = os.environ.get("RAG_MOUNTS", "")
src = f.read_text()
begin = "# ─── BEGIN ALLOWED_BASE_PATHS (auto-generated) ───"
end   = "# ─── END   ALLOWED_BASE_PATHS (auto-generated) ───"
i, j = src.index(begin), src.index(end)
new = src[:i + len(begin)] + "\n" + mounts + "      " + src[j:]
f.write_text(new)
PY
ok "Mounts eingetragen: ${#_paths[@]} Pfad(e)"

# ---------------------------------------------------------------------------
# 4) Container-Images laden
# ---------------------------------------------------------------------------
step "4/8 · Container-Images laden"

if [[ -d "$BUNDLE_DIR/images" ]] && compgen -G "$BUNDLE_DIR/images/*.tar" >/dev/null; then
  for tar in "$BUNDLE_DIR"/images/*.tar; do
    log "    docker load < $(basename "$tar")"
    run docker load -i "$tar"
  done
  ok "Images aus offline-Bundle geladen"
else
  log "    keine offline-Images gefunden — pullt online vom Registry"
  ( cd "$INSTALL_DIR" && run docker compose pull )
  ok "Images vom Registry geholt"
fi

# ---------------------------------------------------------------------------
# 5) Qdrant + Ollama starten
# ---------------------------------------------------------------------------
step "5/8 · Qdrant und Ollama starten"

cd "$INSTALL_DIR"

if [[ $SKIP_OLLAMA -eq 1 ]]; then
  log "    --skip-ollama gesetzt → Ollama wird NICHT als Container gestartet"
  run docker compose up -d qdrant
  services_to_wait=(qdrant)
else
  run docker compose up -d qdrant ollama
  services_to_wait=(qdrant ollama)
fi

run "$INSTALL_DIR/scripts/wait-for.sh" "http://127.0.0.1:6333/readyz" 60
ok "Qdrant bereit"

if [[ $SKIP_OLLAMA -eq 0 ]]; then
  run "$INSTALL_DIR/scripts/wait-for.sh" "http://127.0.0.1:11434/api/tags" 60
  ok "Ollama bereit"
fi

# ---------------------------------------------------------------------------
# 6) Modelle laden
# ---------------------------------------------------------------------------
step "6/8 · KI-Modelle laden"

EMBEDDING_MODEL="$(grep -E '^EMBEDDING_MODEL=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
CHAT_MODEL="$(grep -E '^CHAT_MODEL=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
# Query-rewriter model (small LLM used to resolve follow-up references
# before retrieval). Empty value = reuse CHAT_MODEL — nothing extra to
# pull. Defaults to qwen2.5:7b when unset in .env so the rewriter has
# a fast small model to lean on out of the box.
REWRITE_MODEL="$(grep -E '^REWRITE_MODEL=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
REWRITE_MODEL_DEFAULT="qwen2.5:7b"
REWRITE_MODEL="${REWRITE_MODEL:-$REWRITE_MODEL_DEFAULT}"

if [[ -d "$BUNDLE_DIR/models" ]] && [[ -n "$(ls -A "$BUNDLE_DIR/models")" ]]; then
  log "    offline-Modell-Blobs gefunden → kopiere nach Ollama-Volume"
  if [[ $SKIP_OLLAMA -eq 0 ]]; then
    cid="$(docker compose ps -q ollama)"
    run docker cp "$BUNDLE_DIR/models/." "$cid:/root/.ollama/"
    run docker compose restart ollama
    run "$INSTALL_DIR/scripts/wait-for.sh" "http://127.0.0.1:11434/api/tags" 60
  else
    run cp -r "$BUNDLE_DIR/models/." "/Users/$(logname 2>/dev/null || echo nobody)/.ollama/" 2>/dev/null \
      || log "⚠  Kann Modelle bei --skip-ollama nicht automatisch ablegen — bitte manuell pullen."
  fi
  ok "Modelle aus offline-Bundle eingespielt"
else
  log "    keine offline-Modelle → pullt online via Ollama"
  if [[ $SKIP_OLLAMA -eq 0 ]]; then
    run docker compose exec -T ollama ollama pull "$EMBEDDING_MODEL"
    run docker compose exec -T ollama ollama pull "$CHAT_MODEL"
    # Pull rewriter model only when distinct from CHAT_MODEL —
    # otherwise it's already on disk.
    if [[ -n "$REWRITE_MODEL" && "$REWRITE_MODEL" != "$CHAT_MODEL" ]]; then
      run docker compose exec -T ollama ollama pull "$REWRITE_MODEL"
    fi
  else
    run ollama pull "$EMBEDDING_MODEL"
    run ollama pull "$CHAT_MODEL"
    if [[ -n "$REWRITE_MODEL" && "$REWRITE_MODEL" != "$CHAT_MODEL" ]]; then
      run ollama pull "$REWRITE_MODEL"
    fi
  fi
  ok "Modelle gepullt: $EMBEDDING_MODEL, $CHAT_MODEL${REWRITE_MODEL:+, $REWRITE_MODEL (rewriter)}"
fi

# ---------------------------------------------------------------------------
# 7) Backend starten + Smoke-Test
# ---------------------------------------------------------------------------
step "7/8 · Backend starten und prüfen"

run docker compose up -d backend
run "$INSTALL_DIR/scripts/wait-for.sh" "http://127.0.0.1:${PORT:-8000}/health" 90
ok "Backend antwortet auf /health"

# ---------------------------------------------------------------------------
# 8) systemd-Units installieren
# ---------------------------------------------------------------------------
step "8/8 · systemd-Units installieren"

if [[ -d "$BUNDLE_DIR/systemd" ]]; then
  for u in "$BUNDLE_DIR"/systemd/*.{service,timer}; do
    [[ -e "$u" ]] || continue
    run install -m 644 "$u" "/etc/systemd/system/$(basename "$u")"
  done
  run systemctl daemon-reload
  run systemctl enable --now reineke-rag.service
  if [[ -f /etc/systemd/system/reineke-rag-backup.timer ]]; then
    run systemctl enable --now reineke-rag-backup.timer
  fi
  ok "systemd-Units aktiviert (reineke-rag.service, reineke-rag-backup.timer)"
fi

# ---------------------------------------------------------------------------
log ""
log "══════════════════════════════════════════════════════════════════════════"
log "  ✓ Reineke-RAG läuft unter http://$(hostname):${PORT:-8000}"
log "══════════════════════════════════════════════════════════════════════════"
log ""
log "  Status :  systemctl status reineke-rag"
log "  Logs   :  docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
log "  Backup :  $INSTALL_DIR/scripts/backup.sh"
log "  Deinst.:  $BUNDLE_DIR/uninstall.sh"
log ""
