# backend/

FastAPI-Backend für `rag-qdrant-local`. Komplette Dokumentation siehe
[../README.md](../README.md).

## Lokal starten

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env liegt im Projekt-Root
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

* OpenAPI-UI: <http://localhost:8000/docs>
* Health: <http://localhost:8000/health>

## Tests

```bash
pytest -v
```

## Modul-Layout

| Modul                    | Verantwortung                                              |
| ------------------------ | ---------------------------------------------------------- |
| `main.py`                | FastAPI-App, Routen, Dependency-Injection                  |
| `config.py`              | Pydantic-Settings (`.env` einlesen)                        |
| `database.py`            | SQLite-Engine, Session-Factory                             |
| `models.py`              | SQLAlchemy-Modelle                                         |
| `schemas.py`             | Pydantic-Request/Response-Schemas                          |
| `path_security.py`       | Allow-list + Path-Traversal-Schutz                         |
| `source_scanner.py`      | Filesystem-Walk, Klassifizierung                           |
| `document_loader.py`     | Extraktion aus PDF/DOCX/XLSX                               |
| `office_converter.py`    | LibreOffice-Wrapper für `.doc`/`.xls`                      |
| `chunker.py`             | Text- und Tabellen-Chunking                                |
| `ollama_client.py`       | HTTP-Client für Ollama (`/api/embeddings`, `/api/chat`)    |
| `qdrant_store.py`        | Qdrant-Zugriff mit Pflicht-Filtern                         |
| `ingestion_service.py`   | Komplette Ingest-Pipeline                                  |
| `retrieval_service.py`   | Frage embedden + Qdrant-Suche                              |
| `chat_service.py`        | Prompt-Zusammenbau, Persistenz, Fallback bei Null-Treffern |
| `openai_compat.py`       | OpenAI-kompatibler Adapter (`/v1/chat/completions`)        |
| `utils.py`               | Hashing, IDs, Logging                                      |

## Konventionen

* Typannotationen überall.
* Pydantic-Schemas an den HTTP-Grenzen.
* Pfad-Eingaben werden **niemals** ohne `path_security.resolve_safe_path()`
  weiterverwendet.
* Jede Qdrant-Suche enthält Tenant- und Project-Filter — der Store-Layer
  weigert sich, sie wegzulassen.
