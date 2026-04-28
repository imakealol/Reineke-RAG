#!/usr/bin/env bash
# =============================================================================
# Reineke-RAG · build-bundle.sh
# =============================================================================
# Baut das Auslieferungs-Bundle für eine versionierte Reineke-RAG-Installation.
#
# Das Bundle enthält:
#   * den kompletten installer/-Ordner (install.sh, README, .env.example, …)
#   * das gebaute Backend-Image als Docker-Tar in images/
#   * VERSION + SHA256-Checksumme
#
# NICHT enthalten (auf Wunsch):
#   * Qdrant-Image  → Kunde zieht es zur Install-Zeit aus Docker Hub
#                      ODER legt selbst eine images/qdrant.tar bei
#   * Ollama-Image  → analog
#   * Modell-Blobs  → analog (vor-pullen via `ollama pull` in der models/-Box,
#                      oder online beim ersten Start)
#
# Aufruf:
#   scripts/build-bundle.sh                        # Version aus letztem Tag,
#                                                    Fallback: 1.0.0
#   scripts/build-bundle.sh 1.0.0                  # explizite Version
#   scripts/build-bundle.sh 1.0.0 --release        # zusätzlich:
#                                                    Tag setzen, pushen,
#                                                    GitHub-Release anlegen
#
# Output:
#   dist/reineke-rag-installer-<version>.tar.gz
#   dist/reineke-rag-installer-<version>.tar.gz.sha256
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Argumente
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=""
RELEASE=0

for arg in "$@"; do
  case "$arg" in
    --release) RELEASE=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    [0-9]*.*.*|v[0-9]*.*.*)
      VERSION="${arg#v}"
      ;;
    *)
      echo "Unbekanntes Argument: $arg" >&2
      exit 2
      ;;
  esac
done

# Fallback-Version: letzter Git-Tag, sonst 1.0.0
if [[ -z "$VERSION" ]]; then
  VERSION="$(git -C "$REPO_ROOT" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || true)"
  VERSION="${VERSION:-1.0.0}"
fi

# Validate semver-ish
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]]; then
  echo "Ungültige Version: '$VERSION' (erwartet MAJOR.MINOR.PATCH)" >&2
  exit 1
fi

IMAGE="reineke-rag/backend:${VERSION}"
DIST_DIR="${REPO_ROOT}/dist"
WORK_DIR="${DIST_DIR}/reineke-rag-installer-${VERSION}"
TARBALL="${DIST_DIR}/reineke-rag-installer-${VERSION}.tar.gz"

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
ok()    { echo "  ✓ $*"; }
fail()  { echo "  ✗ $*" >&2; exit 1; }
step()  { echo; echo "── $* ──"; }

# Portable sha256: macOS hat shasum, Linux hat sha256sum
sha256() {
  if command -v sha256sum >/dev/null; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi
}

# ---------------------------------------------------------------------------
# Voraussetzungen
# ---------------------------------------------------------------------------
step "Voraussetzungen prüfen"
command -v docker >/dev/null || fail "Docker fehlt — bitte installieren."
docker info >/dev/null 2>&1   || fail "Docker-Daemon läuft nicht."
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',') verfügbar"

if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
  echo "  ⚠  Working-Tree ist nicht clean — gebündelt wird der CHECKOUT-Stand."
fi

ok "Version: ${VERSION}"
ok "Image:   ${IMAGE}"
ok "Output:  ${TARBALL}"

# ---------------------------------------------------------------------------
# 1) Backend-Image bauen
# ---------------------------------------------------------------------------
step "1/5 · Backend-Image bauen"
docker build \
  --tag "$IMAGE" \
  --label "org.opencontainers.image.version=${VERSION}" \
  --label "org.opencontainers.image.source=https://github.com/rewerner42/Reineke-RAG" \
  "${REPO_ROOT}/rag-qdrant-local/backend"
ok "gebaut: $IMAGE"

# ---------------------------------------------------------------------------
# 2) Bundle-Layout vorbereiten
# ---------------------------------------------------------------------------
step "2/5 · Bundle-Layout anlegen"
rm -rf "$WORK_DIR" "$TARBALL" "${TARBALL}.sha256"
mkdir -p "$WORK_DIR/images"

# Installer-Inhalt rüberkopieren
cp -R "${REPO_ROOT}/rag-qdrant-local/installer/." "$WORK_DIR/"

# VERSION-Marker
echo "$VERSION" > "$WORK_DIR/VERSION"
ok "Layout: $(ls "$WORK_DIR" | tr '\n' ' ')"

# ---------------------------------------------------------------------------
# 3) Backend-Image in tar speichern
# ---------------------------------------------------------------------------
step "3/5 · Backend-Image als Tar speichern"
docker save -o "$WORK_DIR/images/rag-backend-${VERSION}.tar" "$IMAGE"
ok "$(du -h "$WORK_DIR/images/rag-backend-${VERSION}.tar" | cut -f1) → images/rag-backend-${VERSION}.tar"

# ---------------------------------------------------------------------------
# 4) Bundle-spezifische .env-Werte einsetzen
# ---------------------------------------------------------------------------
step "4/5 · Bundle-Defaults in .env.example schreiben"
# An die .env.example einen klar markierten Block anhängen, der das
# gebündelte Image-Tag verbindlich setzt. Das Compose nutzt
# ${BACKEND_IMAGE:-…} und greift hier zu, sobald der Kunde .env anlegt.
if ! grep -q "BUNDLED-VERSION" "$WORK_DIR/.env.example"; then
  cat >>"$WORK_DIR/.env.example" <<EOF

# =============================================================================
# Vom Bundle erzeugte Werte (NICHT manuell ändern — BUNDLED-VERSION ${VERSION})
# =============================================================================
# Das gebündelte Backend-Image wird vom Installer aus images/rag-backend-${VERSION}.tar
# geladen. Compose referenziert es über diese Variable.
BACKEND_IMAGE=${IMAGE}
EOF
fi
ok "BACKEND_IMAGE=${IMAGE}"

# ---------------------------------------------------------------------------
# 5) Tarball + Checksumme
# ---------------------------------------------------------------------------
step "5/5 · Tarball packen und Checksumme erzeugen"
( cd "$DIST_DIR" && tar czf "$(basename "$TARBALL")" "$(basename "$WORK_DIR")" )
( cd "$DIST_DIR" && sha256 "$(basename "$TARBALL")" > "$(basename "$TARBALL").sha256" )

size="$(du -h "$TARBALL" | cut -f1)"
hash="$(awk '{print $1}' "${TARBALL}.sha256")"
ok "Tarball: $TARBALL  ($size)"
ok "SHA256:  $hash"

# ---------------------------------------------------------------------------
# Optionaler Release
# ---------------------------------------------------------------------------
if [[ $RELEASE -eq 1 ]]; then
  step "6 · Git-Tag setzen und GitHub-Release anlegen"

  command -v gh >/dev/null \
    || fail "GitHub-CLI 'gh' fehlt. Installieren oder Release manuell anlegen."

  if git -C "$REPO_ROOT" rev-parse "v${VERSION}" >/dev/null 2>&1; then
    echo "  ⚠  Tag v${VERSION} existiert lokal bereits — überspringe Erstellung."
  else
    git -C "$REPO_ROOT" tag -a "v${VERSION}" -m "Reineke-RAG v${VERSION}"
    ok "Tag v${VERSION} angelegt"
  fi

  echo "  → push tag"
  git -C "$REPO_ROOT" push origin "v${VERSION}"

  echo "  → gh release create"
  gh release create "v${VERSION}" \
    "$TARBALL" "${TARBALL}.sha256" \
    --title "Reineke-RAG v${VERSION}" \
    --notes "$(cat <<EOF
Offline-Installer-Bundle für Kunden-Server-Deployments.

## Inhalt
- \`installer/\` — Installations-Skripte, README, kommentierte \`.env.example\`
- \`images/rag-backend-${VERSION}.tar\` — gebautes Backend-Image
- \`VERSION\` = \`${VERSION}\`

## NICHT enthalten (mit Absicht)
- Qdrant-Image und Ollama-Image — der Installer zieht sie zur Install-Zeit aus Docker Hub. Bei air-gapped Hosts kann der Kunde \`images/qdrant.tar\` und \`images/ollama.tar\` selbst beilegen, bevor \`install.sh\` läuft.
- Ollama-Modell-Blobs — werden über \`ollama pull\` geladen oder per \`models/\`-Verzeichnis im Bundle.

## Installation
\`\`\`
sudo tar xzf reineke-rag-installer-${VERSION}.tar.gz -C /tmp
cd /tmp/reineke-rag-installer-${VERSION}
sudo cp .env.example .env
sudo nano .env   # ALLOWED_BASE_PATHS setzen
sudo ./install.sh
\`\`\`

Details: siehe \`installer/README.md\` im Bundle.

## SHA256
\`\`\`
$(cat "${TARBALL}.sha256")
\`\`\`
EOF
)"
  ok "Release veröffentlicht: $(gh release view "v${VERSION}" --json url --jq .url)"
fi

echo
echo "Fertig."
