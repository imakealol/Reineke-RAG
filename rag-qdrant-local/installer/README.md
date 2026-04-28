# Reineke-RAG · Installer Bundle

Diese Anleitung richtet sich an die **IT-Abteilung des Kunden**.
Sie installiert Reineke-RAG vollständig **offline** auf einem Linux-Server.

> Schreibweise: alle Befehle als `root` (oder mit `sudo`) ausführen.

---

## 1 · Voraussetzungen (vor dem Skript-Start prüfen)

### Hardware

| Komponente | Minimum | Empfohlen |
|---|---|---|
| CPU | 8 Kerne x86_64 | 16 Kerne |
| RAM | 32 GB | 64 GB |
| GPU | – (CPU-Betrieb möglich) | NVIDIA mit ≥ 24 GB VRAM |
| Disk (frei) | 200 GB SSD | 500 GB NVMe |

> Apple-Silicon-Server (Mac mini M4 Pro/Max) werden ebenfalls unterstützt.
> In dem Fall läuft Ollama nativ auf dem Host, nicht im Container — siehe Abschnitt 6.

### Software (muss bereits installiert sein)

- **Linux**: Ubuntu 22.04/24.04 LTS oder RHEL 9 / Rocky 9
- **Docker Engine** ≥ 24 mit **Compose-v2-Plugin**
  Test: `docker compose version` muss ohne Fehler antworten
- **NVIDIA-Treiber + nvidia-container-toolkit** (nur bei GPU-Hosts)
  Test: `docker run --rm --gpus all nvidia/cuda:12-base nvidia-smi`
- **Root- oder Sudo-Zugriff** auf den Zielserver

### Netzwerk / Daten

- **Dokumenten-Freigabe** ist auf dem Host bereits gemountet
  (z. B. SMB unter `/mnt/dms`, NFS unter `/srv/dokumente`).
  Der Installer mountet **keine** Dateisysteme.
- **Internet-Zugriff**: optional. Wenn der Server **kein** Internet hat,
  müssen die Verzeichnisse `images/` und `models/` im Bundle vorhanden sein
  (werden bei der Auslieferung von Reineke-Technik bereitgestellt).
- **Port 8000** (HTTP-API) muss intern erreichbar sein —
  TLS und Reverse-Proxy übernimmt der Kunde nach der Installation.

### Optional (vorab klären, nicht zwingend)

- Single-Sign-On (OIDC) — wird in einer zukünftigen Version unterstützt.
- TLS-Zertifikat für den eigenen Reverse-Proxy (Caddy / nginx / Traefik).
- Mandanten-/Projekt-Bezeichnung (für `ALLOWED_BASE_PATHS`).

---

## 2 · Installation in drei Schritten

### Schritt 1 — Bundle entpacken

```bash
sudo tar xzf reineke-rag-installer.tar.gz -C /tmp
cd /tmp/reineke-rag-installer
```

### Schritt 2 — `.env` ausfüllen

```bash
cp .env.example .env
sudo nano .env          # oder: vim .env
```

Mindestens diese Felder anpassen (Details in der `.env.example` selbst):

- `ALLOWED_BASE_PATHS` → die Verzeichnisse mit den Quell-Dokumenten
- `EMBEDDING_MODEL` und `CHAT_MODEL` → bei abweichender Hardware-Größe

Alle anderen Werte können auf den Defaults bleiben.

### Schritt 3 — Installer starten

```bash
sudo ./install.sh
```

Der Installer:

1. prüft alle Voraussetzungen,
2. legt `/opt/reineke-rag/` mit den Persistenz-Volumes an,
3. lädt die Container-Images (offline aus `images/` oder online vom Registry),
4. startet Qdrant + Ollama,
5. lädt die KI-Modelle (offline aus `models/` oder per `ollama pull`),
6. startet das Backend und ruft `GET /health` ab,
7. installiert systemd-Units, damit das System Reboot-fest läuft,
8. richtet den nächtlichen Backup-Timer ein.

Laufzeit: **5 – 30 Min** (je nach Hardware und ob Modelle gepullt werden müssen).

Am Ende erscheint:

```
✓ Reineke-RAG läuft unter http://<servername>:8000
```

---

## 3 · Was der Installer NICHT macht

Diese Schritte muss die Kunden-IT eigenständig durchführen, weil sie
umgebungsspezifisch sind:

| Aufgabe | Warum manuell |
|---|---|
| Mounten der Dokumenten-Freigabe | Pfade, Credentials und Mount-Optionen sind kunden­spezifisch |
| TLS / Reverse-Proxy davorschalten | Der Kunde nutzt sein bestehendes Caddy/nginx/Traefik |
| OIDC / SSO anbinden | Kommt mit einer späteren Version |
| Firewall-Regeln (Port 8000) | Hängt vom internen Netz ab |
| Monitoring (Prometheus/Grafana) | Nutzt das vorhandene Beobachtungs­system des Kunden |

---

## 4 · Bedienung nach der Installation

| Aufgabe | Befehl |
|---|---|
| Status prüfen | `systemctl status reineke-rag` |
| Logs ansehen | `docker compose -f /opt/reineke-rag/docker-compose.yml logs -f` |
| Stoppen | `systemctl stop reineke-rag` |
| Starten | `systemctl start reineke-rag` |
| Backup manuell | `/opt/reineke-rag/scripts/backup.sh` |
| Restore | `/opt/reineke-rag/scripts/restore.sh <backup-datei.tar.gz>` |
| Update einspielen | Bundle neu entpacken und `sudo ./install.sh` erneut ausführen |
| Komplett entfernen | `sudo /opt/reineke-rag/uninstall.sh` |

Backups landen unter `/opt/reineke-rag/backups/` — täglich um 03:00 Uhr.
Aufbewahrung: 14 Tage (im Backup-Skript einstellbar).

---

## 5 · Fehlersuche

### „Docker required" beim Start

→ Docker ist nicht installiert oder Compose-v2-Plugin fehlt.
Prüfen: `docker compose version`.

### `/health` antwortet mit 503

→ Ollama oder Qdrant ist noch nicht bereit. 1 – 2 Minuten warten,
dann `docker compose -f /opt/reineke-rag/docker-compose.yml logs ollama`
prüfen. Modelle werden bei der ersten Antwort geladen — kann beim
ersten Aufruf bis zu 60 Sekunden dauern.

### „Permission denied" beim Lesen von Dokumenten

→ Der Backend-Container läuft als nicht-Root. Sicherstellen, dass die
Dokumenten-Freigabe für UID 10001 lesbar gemountet ist
(z. B. `mount -o ro,uid=10001 …`) oder die Dateirechte auf `o+r` setzen.

### Modelle laden zu langsam

→ Bei CPU-Betrieb empfohlene Modelle:
`EMBEDDING_MODEL=mxbai-embed-large`, `CHAT_MODEL=qwen2.5:7b`.
Auf GPU-Hosts:
`EMBEDDING_MODEL=bge-m3`, `CHAT_MODEL=qwen2.5:32b-instruct-q4_K_M`.

---

## 6 · Apple-Silicon-Spezialfall (Mac mini M4)

Auf Apple-Silicon läuft Ollama **nativ** (nicht im Container), weil das
Metal-Backend deutlich schneller ist als die Linux-VM-Variante.

Vorgehen:

1. Ollama nativ installieren: `brew install ollama && brew services start ollama`
2. Modelle pullen: `ollama pull bge-m3 qwen2.5:32b-instruct-q4_K_M`
3. In `.env` setzen: `OLLAMA_BASE_URL=http://host.docker.internal:11434`
4. `install.sh` mit `--skip-ollama` aufrufen.

---

## 7 · Bundle-Inhalt

```
reineke-rag-installer/
├── README.md                 ← dieses Dokument
├── install.sh                ← Haupt-Installer
├── uninstall.sh              ← entfernt die Installation wieder
├── docker-compose.yml        ← Definition der drei Services
├── .env.example              ← kommentierte Konfigurations-Vorlage
├── scripts/
│   ├── wait-for.sh           ← wartet auf HTTP-Endpunkt
│   ├── backup.sh             ← SQLite + Qdrant-Snapshot
│   └── restore.sh            ← stellt aus Backup wieder her
├── systemd/
│   ├── reineke-rag.service
│   ├── reineke-rag-backup.service
│   └── reineke-rag-backup.timer
├── images/                   ← (offline) gespeicherte Container-Images
└── models/                   ← (offline) vorgepullte Ollama-Modelle
```

`images/` und `models/` werden nur bei air-gapped-Auslieferungen mitgeliefert.

---

## 8 · Support

- E-Mail: `support@reineke-technik.de`
- Bei Fehlern bitte folgendes mitschicken:
  ```bash
  docker compose -f /opt/reineke-rag/docker-compose.yml logs --tail=200 > /tmp/rag-logs.txt
  /opt/reineke-rag/scripts/diag.sh > /tmp/rag-diag.txt
  ```
