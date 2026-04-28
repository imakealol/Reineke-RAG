# Reineke-RAG

Lokales, vollständig **offline lauffähiges RAG-System** (Retrieval-Augmented
Generation) für Word-, PDF- und Excel-Dokumente. Entwickelt für die interne
Nutzung bei Reineke-Technik — keine Cloud, keine externen API-Aufrufe, alle
Daten bleiben on-premise.

> **Status:** lauffähige Implementierung in [`rag-qdrant-local/`](rag-qdrant-local/).
> Die ursprüngliche Konzeptphase (Docker-Compose-Stack, ADRs, mehrsprachige
> Handbücher) wurde durch diese schlankere FastAPI-Lösung ersetzt.

## Architektur in einem Satz

[FastAPI](https://fastapi.tiangolo.com/) + [Ollama](https://ollama.com/) +
[Qdrant](https://qdrant.tech/), Multi-Tenant über SQLite-Metadaten,
Datei-Ingestion aus serverseitig gemounteten Verzeichnissen, OpenAI-kompatibler
`/chat`-Endpunkt, eingebautes Admin-UI.

![Architektur-Schema](docs/architecture-schema.svg)

## Repo-Layout

| Pfad | Inhalt |
| ---- | ------ |
| [`rag-qdrant-local/`](rag-qdrant-local/) | Backend-Implementierung — Start hier. Eigene `README.md` mit Schnellstart. |
| [`rag-qdrant-local/backend/`](rag-qdrant-local/backend/) | FastAPI-App, Tests, `Dockerfile`, `requirements.txt` |
| [`rag-qdrant-local/backend/app/admin/`](rag-qdrant-local/backend/app/admin/) | Admin-UI (Bootstrap + htmx) |
| [`rag-qdrant-local/docs/`](rag-qdrant-local/docs/) | OpenWebUI-Pipe und Ollama-Tuning-Hinweise |
| [`docs/TECHNISCHE_DOKUMENTATION.md`](docs/TECHNISCHE_DOKUMENTATION.md) | Technische Gesamtdokumentation (deutsch) |
| [`pdf/TECHNISCHE_DOKUMENTATION.pdf`](pdf/TECHNISCHE_DOKUMENTATION.pdf) | Gerendertes PDF derselben Doku |
| [`scripts/md2pdf.py`](scripts/md2pdf.py) | WeasyPrint-Renderer (Markdown → paginiertes PDF) |

## Schnellstart

Voraussetzungen: laufende Instanzen von **Ollama** (`http://localhost:11434`)
und **Qdrant** (`http://localhost:6333`), Python 3.11+, optional LibreOffice
für `.doc`/`.xls`.

```bash
git clone git@github.com:rewerner42/Reineke-RAG.git
cd Reineke-RAG/rag-qdrant-local

cp .env.example .env
# .env editieren — vor allem ALLOWED_BASE_PATHS!

cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Admin-UI: <http://localhost:8000/admin>
API-Docs: <http://localhost:8000/docs>

Vollständige Anleitung inkl. Modell-Pulls, Smoke-Test und OpenWebUI-Anbindung:
[`rag-qdrant-local/README.md`](rag-qdrant-local/README.md).

## Unterstützte Dateitypen

`.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls` — Legacy-Formate (`.doc`, `.xls`)
werden über `soffice` (LibreOffice) konvertiert; alle übrigen Formate laufen
nativ.

## Tests

```bash
cd rag-qdrant-local/backend
pytest
```

## Dokumentation rendern

```bash
python scripts/md2pdf.py \
  docs/TECHNISCHE_DOKUMENTATION.md \
  pdf/TECHNISCHE_DOKUMENTATION.pdf \
  --schema docs/architecture-schema.svg
```

## Lizenz

Internes Projekt der Reineke-Technik. Keine Veröffentlichungslizenz
festgelegt — Nutzung außerhalb des Unternehmens nur nach Absprache.
