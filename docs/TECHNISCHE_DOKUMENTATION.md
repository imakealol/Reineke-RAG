---
title: "Reineke-RAG · Technische Dokumentation"
subtitle: "Lokales, multi-tenant RAG-System (rag-qdrant-local) · Version 2"
author: "Reineke-Technik"
date: "Stand 21. Mai 2026"
---

# 1 · Überblick

**Reineke-RAG** ist ein vollständig **offline** betriebenes Retrieval-Augmented-Generation-System
für die Beantwortung natürlich­sprachiger Fragen über interne Dokumente (PDF, Word, Excel,
PowerPoint, OpenDocument, HTML — auch Legacy-Formate `.doc` und `.xls`). Die aktuelle
Code­basis liegt unter `rag-qdrant-local/` und umfasst rund **5.300 Zeilen Python** in
einem FastAPI-Backend mit eingebautem Admin-Web-UI.

| Eigenschaft | Wert |
|---|---|
| Einsatzart | Server-seitig, vollständig on-premise |
| Internet-Zugriff | nicht erforderlich |
| Quell-Dokumente | Datei­system + MediaWiki-XML-Import |
| Unterstützte Formate | PDF, DOCX, DOC, XLSX, XLS, PPTX, ODT, HTML, HTM |
| Mandanten­fähig | ja (Tenant + Project, pro Paar ein „Agent") |
| Halluzinations­schutz | Score-Schwelle · Quellen­zwang · Meta-Frage-Abfang · Quellen-Trailer-Bereinigung |
| LLM-Laufzeit | Ollama (Apple-Silicon-tauglich) |
| Vektor-DB | Qdrant |
| Reranker | bge-reranker-v2-m3 (Cross-Encoder, lazy, ~2 GB) |
| Metadaten-Store | SQLite (WAL-Mode) — **10 Tabellen** |
| API-Stil | FastAPI, OpenAPI 3, OpenAI-kompatibel |
| Admin-UI | Bootstrap 5 + htmx, integriert (`/admin/`) |
| Zeitplanung | APScheduler — tägliche Auto-Ingest-Jobs |

Das System ist so konzipiert, dass es ausschließlich auf vom Administrator
freigegebenen Verzeichnis­bäumen arbeitet. Datei-Inhalte verlassen die Maschine nicht.
Quell-Verzeichnisse werden **read-only** gelesen.

## 1.1 Was ist neu in Version 2

Gegenüber dem Auslieferungs­stand vom 28. April 2026 sind folgende Bausteine hinzugekommen:

* **Admin-Web-UI** unter `/admin/` — Bootstrap + htmx, 8 Seiten (Übersicht, Agenten,
  Dokumente, Ingest-Assistent, Zeitplan, Ingest-Verlauf, Logs, Konfiguration).
* **Reranker-Schicht** — BAAI/bge-reranker-v2-m3 Cross-Encoder, lazy geladen,
  pro Kollektion automatisch ab 100 Dokumenten aktiv (override­bar).
* **Auto-Ingest-Scheduler** — pro Eintrag ein täglicher Cron-Job (HH:MM UTC),
  integrierter `IngestSchedule`-Tabelle.
* **MediaWiki-Connector** — XML-Export + Uploads + LocalSettings → Pages + Files mit
  klick­baren Wiki-URLs in den Quellen­zitaten.
* **Erweiterte Dokumenten-Loader** — neu: PPTX (slide-weise), ODT (über LibreOffice),
  HTML/HTM (BeautifulSoup, Noise-Stripping, Tabellen als Markdown).
* **Per-Agent-Persona-Prompts**, **per-Agent-Chat-Modell**, **per-Agent-Reranker**
  in einer eigenen Tabelle `tenant_project_prompts`.
* **Runtime-Settings-Overrides** — 13 ausgewählte Schlüssel sind ohne Neustart
  im Admin-UI änderbar (`settings_overrides`-Tabelle).
* **Globaler System-Prompt-Override** (`system_prompt_overrides`-Tabelle).
* **Cross-Turn-Citation-Recall** — Zitate früherer Antworten flossen wieder in
  den Kandidaten-Pool ein, damit Folgefragen wie „die andere Quelle, die du genannt hast"
  funktionieren.
* **Query-Synonym-Expansion** — hand-kuratierte DACH-Synonyme (z. B. Dienstleister ↔
  Lieferant) werden vor dem Embedding angehängt.
* **Stem-basierte Dedup** — identische Dokumente in mehreren Formaten (PDF + DOCX)
  belegen zusammen nur noch einen Top-K-Platz.
* **Datei­namen-Präfix in jedem Chunk** — „[Datei: …]" am Chunk-Anfang, damit
  name-zentrische Fragen den richtigen Treffer holen.
* **Meta-Frage-Abfang** — „wie viele Dokumente?" liefert die echte SQLite-Zahl
  statt eines halluzinierten LLM-Werts.
* **Ollama num_ctx-Auto-Detect** über `/api/show` — kein stilles Truncieren
  bei Modellen mit ≥ 8 k Kontext.
* **Pro-Job-Logging + Live-Tail** — Server-Sent-Events streamen
  Anwendungs- und Request-Logs in das Admin-UI.
* **Request-Log-Middleware** — Audit-Trail aller HTTP-Anfragen
  (`request_logs`-Tabelle, Filter im UI).
* **Strukturierte `sources`** in OpenAI-Antworten — zusätzlich zum
  Markdown-Quellen­block, mit `source_type` und `url` (für Wiki-Klicks).
* **Filename-Junk-Filter** — `~$lock`-Dateien, `._`-Resource-Forks und Zero-Byte
  Stubs werden beim Scan automatisch ignoriert.

---

# 2 · Architektur

Das nachfolgende Schema (eine Seite, separat enthalten) zeigt die fünf Schichten —
Client, API, Sicherheits-Gate, Service, Adapter/Parser sowie Persistenz/externe Dienste —
und die Datenflüsse zwischen ihnen.

![Architektur-Schema](architecture-schema.svg)

**Schichten in Kurzform**

1. **Client-Schicht** — Open WebUI · **Admin-Web-UI (NEU)** · Browser/cURL · Admin-Skripte
2. **API-Schicht** — FastAPI auf Port 8000, native Endpunkte plus OpenAI-Kompatibilität
   plus `/admin/api/*` (30+ HTMX-Endpunkte)
3. **Sicherheits-Gate** — Pfad-Allow-list und erzwungene Mandanten-Filter
4. **Service-Schicht** — `IngestionService`, `RetrievalService`, `ChatService`,
   **`SchedulerService` (NEU)**, **`MediaWikiImportService` (NEU)**
5. **Adapter & Parser** — Dokumenten-Loader (PDF, DOCX, XLSX, PPTX, ODT, HTML),
   Office-Konverter, Chunker, Ollama-Client, Qdrant-Store, **Reranker (NEU)**
6. **Persistenz & externe Dienste** — SQLite, Qdrant (`:6333`), Ollama (`:11434`),
   Datei­system

---

# 3 · Technologie-Komponenten

## 3.1 Laufzeit & Frameworks

| Komponente | Version | Zweck |
|---|---|---|
| Python | 3.11 (Container) / 3.12 (Tests) | Programmier­sprache |
| FastAPI | 0.115.0 | HTTP-API, OpenAPI-Spezifikation |
| Uvicorn | 0.30.6 | ASGI-Server |
| Pydantic | 2.9.2 | Datenvalidierung, Schemata |
| pydantic-settings | 2.5.2 | `.env`-Konfiguration |
| python-dotenv | 1.0.1 | Environment-Loader |
| httpx | 0.27.2 | Async HTTP-Client (Ollama) |
| SQLAlchemy | 2.0.36 | ORM für SQLite |
| aiosqlite | 0.20.0 | Async-Treiber |
| qdrant-client | 1.12.1 | Qdrant SDK |
| python-multipart | 0.0.12 | Multipart-Form-Daten |
| **Jinja2** | **3.1.4** | Admin-UI-Templates (NEU) |
| **APScheduler** | **3.10.4** | Auto-Ingest-Scheduler (NEU) |
| **FlagEmbedding** | **1.3.4** | Cross-Encoder-Reranker (NEU) |

## 3.2 Dokumenten-Parsing

| Format | Parser | Strategie |
|---|---|---|
| `.pdf` | pypdf 5.0.1 | seitenweise Text-Extraktion, Erkennung Image-only PDFs |
| `.docx` | python-docx 1.1.2 | Paragraphen + Tabellen → Markdown |
| `.xlsx` | openpyxl 3.1.5 | header-erkennend, zeilen­basiertes Chunking |
| `.pptx` | python-pptx 1.0.2 | slide-weise, Tabellen, Speaker Notes (NEU v2) |
| `.odt` | LibreOffice → `.docx` | über `office_converter.py` (NEU v2) |
| `.html` / `.htm` | BeautifulSoup 4.12.3 | Main-Region · Noise-Stripping · Tabellen als Markdown |
| `.doc` | LibreOffice → `.docx` | über `office_converter.py` |
| `.xls` | LibreOffice → `.xlsx` | über `office_converter.py` |

## 3.3 KI-Komponenten (lokal)

| Aufgabe | Modell-Default | Empfohlen (M4 Max) | Dimension / Parameter |
|---|---|---|---|
| Embedding | `mxbai-embed-large` | **`bge-m3`** (mehrsprachig) | 1024 |
| Chat-Generierung | `qwen2.5:14b` | `qwen2.5:32b-instruct-q4_K_M` | — |
| **Reranking (NEU)** | `BAAI/bge-reranker-v2-m3` | dito | 568 M, lazy geladen |

Embedding und Chat laufen über **Ollama** (`http://localhost:11434`) — keine Cloud-Anbindung.
Der Reranker läuft als Python-In-Process-Modell (über FlagEmbedding/PyTorch) und wird
erst beim ersten Aufruf in den Speicher geladen (5–15 s Cold-Start).

Pro Agent (`(tenant, project)`) lässt sich das Chat-Modell sowie der Reranker-Status
über das Admin-UI individuell konfigurieren (`tenant_project_prompts`).

## 3.4 Datenhaltung

| Speicher | Pfad / Endpunkt | Zweck |
|---|---|---|
| SQLite | `storage/rag.sqlite` (WAL) | 10 Tabellen — Metadaten, Audit, Chat-Verlauf, Konfiguration |
| Qdrant | `http://localhost:6333` | Vektor-Index (Cosine) |
| Datei­system | `ALLOWED_BASE_PATHS` | Original-Dokumente (read-only) |
| Job-Logs | `storage/job-logs/<job_id>.log` | Pro-Ingest-Log (rotierend, NEU v2) |
| Anwendungs-Log | `storage/logs/app.log` | Rotierend (5×5 MB, NEU v2) |

## 3.5 Container & Deployment

* **Dockerfile** vorhanden (`backend/Dockerfile`) — Python 3.11-slim + LibreOffice + curl.
* **Installer-Bundle** (`installer/`) — `install.sh`, `docker-compose.yml`,
  systemd-Units, Backup-Timer — siehe Abschnitt 13.
* Externe Dienste (Qdrant, Ollama) werden als Side-Cars / separate Container betrieben.
* Volumes: `storage/rag.sqlite`, `storage/converted/`, `storage/temp/`,
  `storage/job-logs/`, `storage/logs/`.

---

# 4 · Funktionsverzeichnis

Das Backend ist in **23 Python-Module** unter `backend/app/` (+ Admin-UI-Submodul,
+ Connectors-Submodul) aufgeteilt. Die folgenden Tabellen listen sämtliche
öffentlichen Funktionen, Klassen und Methoden auf (strukturiert nach Modul,
sortiert nach Aufruf­tiefe).

## 4.1 `config.py` — Konfiguration

| Symbol | Typ | Zweck |
|---|---|---|
| `Settings` | Klasse | Pydantic-`BaseSettings` mit allen `.env`-Werten |
| `Settings.allowed_base_paths` | Property | Parst `ALLOWED_BASE_PATHS` als `List[Path]` |
| `Settings.sqlite_path` | Property | Aufgelöster Pfad zur SQLite-Datei |
| `Settings.converted_dir` | Property | Verzeichnis für Office-Konvertierungs­ergebnisse |
| `Settings.temp_dir` | Property | Verzeichnis für temporäre Dateien |
| `Settings.job_logs_dir` | Property | Pro-Job-Logverzeichnis (NEU v2) |
| `_project_root() -> Path` | Funktion | Ermittelt Projekt-Root unabhängig vom CWD |
| `get_settings() -> Settings` | Funktion | Cached Singleton (`@lru_cache`) |

Neue Schlüssel in v2: `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_CTX`, `RERANK_ENABLED`,
`RERANK_MODEL`, `RERANK_OVERFETCH_K`, `RERANK_AUTO_ENABLE_MIN_DOCS`,
`CHAT_HISTORY_TURNS`.

## 4.2 `database.py` — SQLite Engine & Sessions

| Symbol | Signatur | Zweck |
|---|---|---|
| `_build_engine()` | `() -> Engine` | Erstellt SQLAlchemy-Engine mit WAL + 30-s-Busy-Timeout |
| `init_db()` | `() -> None` | Erstellt alle Tabellen aus `models.Base.metadata` |
| `session_scope()` | `() -> Iterator[Session]` | Context-Manager mit Commit/Rollback |
| `get_db()` | `() -> Iterator[Session]` | FastAPI-Dependency |
| `SessionLocal` | `sessionmaker` | Für Admin-API-Callers außerhalb von Depends |

## 4.3 `models.py` — ORM-Klassen (10 Tabellen)

| Klasse | Tabelle | Wichtige Felder |
|---|---|---|
| `FileSource` | `file_sources` | id · tenant · project · base_path · recursive · created_at · last_scan_at · last_ingest_at |
| `Document` | `documents` | id · tenant · project · source_path · file_name · file_extension · file_size · checksum · modified_at · status · chunks_count · error_message · **source_type · source_metadata_json** · created_at · updated_at |
| `IngestionJob` | `ingestion_jobs` | id · tenant · project · source_path · status · files_found · files_indexed · files_skipped · files_failed · chunks_created · **current_file** · error_message · created_at · completed_at |
| `ChatSession` | `chat_sessions` | id · tenant · project · created_at · messages (1:n) |
| `ChatMessage` | `chat_messages` | id · session_id · role · content · sources_json · created_at |
| **`RequestLog`** | `request_logs` | id · created_at · method · path · query_string · status_code · duration_ms · tenant · project · client_ip · error_message **(NEU v2)** |
| **`IngestSchedule`** | `ingest_schedules` | id · tenant · project · base_path · recursive · reindex_changed_only · hour · minute · enabled · last_run_at · last_status · last_error · last_indexed/skipped/failed/chunks · last_job_id · created_at · updated_at **(NEU v2)** |
| **`TenantProjectPrompt`** | `tenant_project_prompts` | PK (tenant, project) · persona_prompt · chat_model · rerank_enabled · rerank_overfetch_k · rerank_model · updated_at **(NEU v2)** |
| **`SystemPromptOverride`** | `system_prompt_overrides` | id (`'global'`) · prompt · updated_at **(NEU v2)** |
| **`SettingsOverride`** | `settings_overrides` | key · value · updated_at **(NEU v2)** |

`Document.status` ∈ {`pending`, `indexed`, `failed`, `deleted`, `requires_ocr`, `empty`}.
`Document.source_type` ∈ {`filesystem`, `mediawiki_page`, `mediawiki_upload`}.

## 4.4 `schemas.py` — Pydantic-DTOs

| Schema | Zweck |
|---|---|
| `TenantProject` | Mixin für `tenant`+`project` |
| `FileEntry` | Datei aus Scan-Resultat |
| `HealthCheckItem`, `HealthResponse` | `/health`-Antwort (jetzt inkl. `reranker`) |
| `ScanPathRequest`, `ScanPathResponse` | `/sources/scan-path` |
| `IngestPathRequest` *(jetzt mit `include_extensions`)*, `IngestPathResponse`, `IngestError` | `/sources/ingest-path` |
| `ReindexChangedRequest` | `/documents/reindex-changed` |
| `IngestScheduleIn`, `IngestScheduleOut` | Auto-Ingest-Scheduler (NEU v2) |
| `TenantProjectPromptIn` | Persona-Prompt-Pflege (NEU v2) |
| `DocumentOut`, `DocumentListResponse` | `GET /documents` |
| `DeleteDocumentResponse` | `DELETE /documents/{id}` |
| `ChatRequest`, `ChatSource` *(jetzt mit `source_type`, `url`)*, `ChatResponse` | `/chat` |
| `RetrieveRequest`, `RetrieveResponse` | `/retrieve` (LLM-frei, NEU v2) |
| `OpenAIMessage`, `OpenAIChatCompletionRequest`, `OpenAIChatCompletionResponse`, `OpenAIModelEntry`, `OpenAIModelList` | OpenAI-Kompatibilität |

## 4.5 `main.py` — FastAPI-App & Endpunkte

| Endpunkt | Methode | Funktion | Beschreibung |
|---|---|---|---|
| `/health` | GET | `health()` | Pingt Ollama, Qdrant, Modelle, **Reranker (NEU)** |
| `/sources/scan-path` | POST | `sources_scan_path()` | Scannt Verzeichnis ohne zu indexieren |
| `/sources/ingest-path` | POST | `sources_ingest_path()` | Vollständiger Ingest mit Embedding + Upsert |
| `/sources/mediawiki/import-xml` | POST | `sources_mediawiki_import_xml()` | MediaWiki-XML-Import (NEU v2) |
| `/documents` | GET | `list_documents()` | Listet indizierte Dokumente |
| `/documents/reindex-changed` | POST | `reindex_changed()` | Reindex nur geänderter Dateien |
| `/documents/{document_id}` | DELETE | `delete_document()` | Löscht Dokument + Vektoren |
| `/chat` | POST | `chat_endpoint()` | RAG-Frage/Antwort mit Quellen |
| `/retrieve` | POST | `retrieve_endpoint()` | LLM-frei — nur Top-K Quellen (NEU v2) |
| `/v1/models` | GET | `openai_models()` | OpenAI-kompatible Modell-Liste |
| `/v1/chat/completions` | POST | `openai_chat_completions()` | OpenAI-kompatibler Chat-Endpunkt |
| `/` | GET | Redirect | Redirect auf `/admin/` (NEU v2) |

Lebenszyklus (`lifespan`): `init_db()` → `apply_overrides()` → `num_ctx`-Probe →
`scheduler.start()`. Shutdown stoppt den Scheduler.

## 4.6 `path_security.py` — Pfad-Sicherheit

| Symbol | Signatur | Zweck |
|---|---|---|
| `PathSecurityError` | Exception | Eingabe nicht erlaubt |
| `_is_within(child, parent)` | `(Path, Path) -> bool` | Prüft Eltern-Beziehung |
| `_is_under_system_deny(path, allowed_bases)` | `(Path, List[Path]) -> bool` | Schutz vor `/etc`, `/root` etc. |
| `get_allowed_base_paths()` | `() -> List[Path]` | Liest und validiert `ALLOWED_BASE_PATHS` |
| `resolve_safe_path(user_input)` | `(str) -> Path` | **Hauptfunktion**: kanonisiert + prüft |
| `assert_existing_dir(path)` | `(Path) -> Path` | Prüft Existenz und Verzeichnis-Typ |

System-Deny-Liste (Default): `/etc`, `/root`, `/home`, `/var`, `/proc`, `/sys`, `/dev`,
`/boot`, `/usr`, `/bin`, `/sbin`, `/lib`, `/opt`.

## 4.7 `source_scanner.py` — Datei-Scanner

| Symbol | Signatur | Zweck |
|---|---|---|
| `SUPPORTED_EXTENSIONS` | Konstante | `{.pdf, .docx, .doc, .xlsx, .xls, .html, .htm, .pptx, .odt}` |
| `_IGNORED_DIR_NAMES` | Konstante | `.git`, `__pycache__`, `node_modules`, `.idea`, `.DS_Store` |
| `_IGNORED_FILE_PREFIXES` | Konstante | `~$`, `._`, `.` (NEU v2 — Office-Locks, macOS-Forks, Dotfiles) |
| `ScanResult` | Dataclass | Felder: `supported`, `unsupported`, `file_types`, `mediawiki_hint` |
| `ScanResult.filter_to_extensions(allowed)` | Methode | Whitelist-Filter (NEU v2) |
| `MediaWikiExportHint` | Dataclass | XML-Pfad + Uploads + LocalSettings-Auszug (NEU v2) |
| `_iter_files(root, recursive)` | Iterator | Datei-Iteration mit Ignore-Liste |
| `_is_junk_file(p)` | `(Path) -> bool` | NEU v2 — überspringt Office-Locks etc. |
| `_classify(path)` | `(Path) -> tuple[bool, str]` | (unterstützt?, Endung) |
| `detect_mediawiki_export(root)` | `(Path) -> Optional[MediaWikiExportHint]` | NEU v2 — `<mediawiki`-Sniff + LocalSettings-Parse |
| `scan_directory(root, *, recursive=True)` | `(Path) -> ScanResult` | **Haupt­funktion** |

## 4.8 `document_loader.py` — Dokumenten-Parsing

| Symbol | Signatur | Zweck |
|---|---|---|
| `DocumentLoadError` | Exception | Allgemeiner Lade-Fehler |
| `RequiresOCRError` | Exception | PDF ohne Text-Layer (Bild-PDF) |
| `LoadedSegment` | Dataclass | Ein Textblock + Metadaten |
| `LoadedDocument` | Dataclass | Sammlung aller Segmente eines Dokuments |
| `_load_pdf(path)` | `(Path) -> List[LoadedSegment]` | Seitenweises Extrahieren |
| `_table_to_markdown(table)` | `(table) -> str` | Word-Tabelle als Markdown |
| `_load_docx(path)` | `(Path) -> List[LoadedSegment]` | Paragraphen + Tabellen |
| `_load_xlsx(path)` | `(Path) -> List[LoadedSegment]` | Sheet-Erkennung, Header-Heuristik |
| `_load_pptx(path)` | `(Path) -> List[LoadedSegment]` | Slide-weise + Tabellen + Speaker Notes (NEU v2) |
| `_load_odt(path)` | `(Path) -> List[LoadedSegment]` | LibreOffice → docx (NEU v2) |
| `_html_table_to_markdown(table)` | `(bs4 Tag) -> str` | HTML-Tabelle als Markdown |
| `_load_html(path)` | `(Path) -> List[LoadedSegment]` | BeautifulSoup-Parsing, Noise-Filter, Tabellen-Extraktion |
| `load_document(path)` | `(Path) -> LoadedDocument` | **Dispatcher** für alle Formate |

## 4.9 `office_converter.py` — Legacy-Office-Konvertierung

| Symbol | Signatur | Zweck |
|---|---|---|
| `OfficeConversionError` | Exception | LibreOffice-Fehler / fehlt |
| `libreoffice_available()` | `() -> bool` | Prüft `soffice` im PATH |
| `_run_soffice(src, target_format, outdir)` | `(Path, str, Path) -> Path` | Subprocess-Wrapper, 180 s Timeout |
| `convert_doc_to_docx(src, outdir=None)` | `(Path) -> Path` | `.doc` → `.docx` |
| `convert_xls_to_xlsx(src, outdir=None)` | `(Path) -> Path` | `.xls` → `.xlsx` |
| `convert_odt_to_docx(src, outdir=None)` | `(Path) -> Path` | `.odt` → `.docx` (NEU v2) |

## 4.10 `chunker.py` — Chunking-Strategien

| Symbol | Signatur | Zweck |
|---|---|---|
| `Chunk` | Dataclass | Embed-fähiger Textblock + Metadaten |
| `_filename_prefix(file_name)` | `(str) -> str` | „[Datei: …] "-Header (NEU v2) |
| `_split_paragraphs(text)` | `(str) -> List[str]` | Trennung an Leerzeilen |
| `_split_long_paragraph(p, size)` | `(str, int) -> List[str]` | Satz-bewusster Hard-Wrap |
| `_chunk_text(text, size, overlap)` | `(str, int, int) -> List[str]` | Sliding Window |
| `_chunk_text_segment(seg, start_index)` | `(LoadedSegment, int) -> List[Chunk]` | Wrapper für Text-Segmente |
| `_xlsx_rows_per_chunk(num_columns)` | `(int) -> int` | Adaptive Block-Größe |
| `_chunk_spreadsheet_segment(seg, start_index)` | `(LoadedSegment, int) -> List[Chunk]` | Tabellen-Chunks (Header wiederholt) |
| `_enforce_char_budget(block, header, budget)` | `(...) -> List[tuple]` | XLSX-Hardcap |
| `chunk_document(doc)` | `(LoadedDocument) -> List[Chunk]` | **Dispatcher** |

Default-Werte: `CHUNK_SIZE=1000`, `CHUNK_OVERLAP=150`, `XLSX_ROWS_PER_CHUNK=40`,
`XLSX_MAX_CHARS_PER_CHUNK=6000`.

## 4.11 `ollama_client.py` — LLM-Client

| Methode | Signatur | Zweck |
|---|---|---|
| `OllamaError` | Exception | API- oder Netzwerk-Fehler |
| `OllamaClient.__init__(base_url=None, timeout=600.0)` | Konstruktor | DI über Settings |
| `_request(method, path, *, json=None)` | async | Low-level HTTP, 1 Retry bei transienten Drops (NEU v2) |
| `list_models()` | `() -> List[str]` | `GET /api/tags` |
| `has_model(name)` | `(str) -> bool` | Prefix-Match |
| `embed(text, *, model=None)` | `(str) -> List[float]` | `POST /api/embeddings` mit `keep_alive` |
| `embed_many(texts, *, model=None)` | `(List[str]) -> List[List[float]]` | Sequenzielle Calls |
| `get_context_length(model)` | async, `(str) -> int` | NEU v2 — `/api/show`-Probe, cached |
| `chat(messages, *, model=None, temperature=None, max_tokens=None)` | `(...) -> str` | `POST /api/chat` mit aufgelöstem `num_ctx` |
| `ping()` | `() -> bool` | Health-Probe |

## 4.12 `qdrant_store.py` — Vektor-Store

| Methode | Signatur | Zweck |
|---|---|---|
| `QdrantStoreError` | Exception | Inkonsistenter Aufruf / API-Fehler |
| `SearchHit` | Dataclass | `score`, `payload`, `point_id` |
| `QdrantStore.__init__(url=None, api_key=None, collection=None)` | Konstruktor | DI über Settings |
| `ping()` | `() -> bool` | Erreichbarkeit |
| `ensure_collection(vector_size)` | `(int) -> None` | Erstellt oder prüft Collection (Cosine) |
| `_ensure_payload_indexes()` | `() -> None` | Idempotente Index-Erstellung (tenant, project, document_id, file_extension) |
| `upsert_chunks(*, document_id, vectors, payloads)` | `(...) -> int` | Schreibt Punkte (Wait=True) |
| `delete_document(document_id)` | `(str) -> int` | Löscht alle Punkte eines Dokuments |
| `search(*, tenant, project, query_vector, top_k, score_threshold=None)` | `(...) -> List[SearchHit]` | **Pflicht-Filter**: tenant + project, sonst Exception |
| `get_points_by_ids(*, tenant, project, point_ids)` | `(...) -> List[SearchHit]` | NEU v2 — Recall früherer Zitate |

## 4.13 `reranker.py` — Cross-Encoder-Reranker *(NEU v2)*

| Methode | Signatur | Zweck |
|---|---|---|
| `RerankerError` | Exception | Modell-Lade-Fehler, Caller fällt auf Bi-Encoder zurück |
| `_RerankerWrapper.__init__(model_name)` | Konstruktor | Lädt FlagReranker, ~2 GB, fp16 |
| `_RerankerWrapper.score(query, passages)` | `(...) -> List[float]` | Normalisierte Cross-Encoder-Scores |
| `get_reranker(model_name=None)` | `(str?) -> _RerankerWrapper` | Lazy Singleton pro Modellname |
| `is_loaded(model_name=None)` | `(str?) -> bool` | Reranker-Cold-Start-Indikator (für `/health`) |
| `loaded_model_names()` | `() -> List[str]` | Aktuell residente Modelle |
| `rerank(*, query, passages, model_name=None)` | `(...) -> List[float]` | Öffentliche Funktion — gibt Scores in Eingangs­reihenfolge zurück |

## 4.14 `rerank_settings.py` — Effektive Reranker-Einstellungen *(NEU v2)*

| Methode | Signatur | Zweck |
|---|---|---|
| `EffectiveRerankSettings` | Dataclass | `enabled`, `overfetch_k`, `model` + Quellen-Marker |
| `smart_default_overfetch_k(doc_count)` | `(int) -> int` | Bucketed: 15 / 30 / 50 / 100 |
| `smart_default_rerank_enabled(doc_count)` | `(int) -> bool` | Auto ab `RERANK_AUTO_ENABLE_MIN_DOCS` |
| `count_indexed_documents(db, *, tenant, project)` | `(...) -> int` | Doc-Count für Auto-Logik |
| `resolve(db, *, tenant, project)` | `(...) -> EffectiveRerankSettings` | Drei-Schichten-Resolver (Override → Smart-Default → Global) |

## 4.15 `ingestion_service.py` — Ingest-Pipeline

| Methode | Signatur | Zweck |
|---|---|---|
| `IngestionService.__init__(ollama=None, store=None)` | Konstruktor | DI |
| `_ensure_collection_for_model()` | async | Probt Embedding-Dimension, erstellt Collection |
| `_upsert_file_source(...)` | sync | Legt/aktualisiert `FileSource`-Datensatz |
| `_find_or_create_document(...)` | sync | (Document, is_new) |
| `_update_job_progress(...)` | static | Schreibt laufende Zähler in `IngestionJob` (NEU v2) |
| `ingest_path(db, *, tenant, project, path, recursive=True, reindex_changed_only=True, include_extensions=None, job_id=None)` | async | **Komplette Pipeline**; `include_extensions=None` → alle, leere Liste → nichts; `job_id` für UI-Live-Progress (NEU v2) |
| `_index_one(*, file_path, document, new_checksum)` | async | Pfad → Loaded → delegate |
| `_index_loaded_document(*, document, loaded, checksum, payload_extras=None)` | async | NEU v2 — Connector-Einstieg ohne Tempfile |
| `_build_payloads(*, document, chunks, checksum, payload_extras=None)` | static | Qdrant-Payload-Aufbau |
| `reindex_changed(db, ...)` | async | Differenz-Reindex |
| `delete_document(db, document_id)` | async | Qdrant + SQLite löschen |

## 4.16 `retrieval_service.py` — Suche + Reranking

| Methode | Signatur | Zweck |
|---|---|---|
| `_document_stem(file_name)` | `(str) -> str` | Stem für Dedup (NEU v2) |
| `_dedup_by_stem(hits)` | `(List[SearchHit]) -> List[SearchHit]` | PDF+DOCX-Duplikate kollabieren (NEU v2) |
| `_merge_unique_by_point_id(primary, extras)` | `(...) -> List` | Cross-Turn-Recall-Merge (NEU v2) |
| `_expand_query_with_synonyms(question)` | `(str) -> str` | DACH-Synonyme anhängen (NEU v2) |
| `RetrievalService.__init__(ollama, store, session_factory, rerank_fn)` | Konstruktor | DI |
| `_load_rerank_fn()` | sync | Lazy-Loader für Reranker (NEU v2) |
| `_resolve_rerank(*, tenant, project)` | sync | Per-Agent-Resolver (NEU v2) |
| `retrieve(*, tenant, project, question, top_k, min_score, rerank_override, past_citation_ids)` | async, `() -> List[SearchHit]` | Embed + Search + (Cross-Turn-Recall + Rerank) + Dedup + Top-K |

## 4.17 `chat_service.py` — Chat-Orchestrierung

| Methode | Signatur | Zweck |
|---|---|---|
| `DEFAULT_SYSTEM_PROMPT` | Konstante | Deutsche RAG-Anweisung (vom DB-Override überschreibbar) |
| `SYSTEM_PROMPT` | Alias | Backwards-Compat (für Tests/Previews) |
| `NO_CONTEXT_ANSWER` | Konstante | Fallback bei 0 Treffern |
| `hits_to_sources(hits)` | Funktion | NEU v2 — Top-Level, von `/retrieve` mitgenutzt |
| `ChatService.__init__(retrieval, ollama, session_factory)` | Konstruktor | DI |
| `_resolve_session_id(...)` | sync | DB-Phase A — Session laden/anlegen |
| `_load_persona(tenant, project)` | sync | Persona-Prompt aus DB (NEU v2) |
| `_load_chat_model(tenant, project)` | sync | Per-Agent-Chat-Modell (NEU v2) |
| `_compose_system_prompt(tenant, project, persona, global_prompt)` | static | Globaler Prompt + Persona-Block (NEU v2) |
| `_load_recent_messages(session_id, *, turns)` | sync | History-Loader (NEU v2) |
| `_is_meta_count_question(question)` | static | Erkennt „wie viele Dokumente?" (NEU v2) |
| `_meta_count_answer(tenant, project)` | sync | Echte SQLite-Zahl (NEU v2) |
| `_load_recent_citation_ids(session_id, *, turns, per_turn)` | sync | Cross-Turn-Recall-IDs (NEU v2) |
| `_persist_messages(...)` | sync | DB-Phase C — User+Assistant persistieren |
| `_build_context(hits)` | static | Quellen­block aufbauen |
| `_format_sources_block(sources)` | static | NEU v2 — deterministische Quellen-Sektion (mit URL für Wiki) |
| `_strip_llm_sources_trailer(answer)` | static | NEU v2 — Halluzinierte „Quellen:" entfernen |
| `_ensure_sources_appended(answer, sources_block)` | static | Final-Trailer setzen (NEU v2) |
| `chat(*, tenant, project, question, session_id, top_k)` | async, `() -> ChatResponse` | **Haupt­methode** |

## 4.18 `scheduler_service.py` — Auto-Ingest-Scheduler *(NEU v2)*

| Symbol | Signatur | Zweck |
|---|---|---|
| `IngestSchedulerService.start()` / `.shutdown()` | sync | Lifecycle, hängt an FastAPI-Lifespan |
| `IngestSchedulerService.reload_schedules()` | sync | Reconcile In-Memory ↔ DB (idempotent) |
| `IngestSchedulerService.trigger_now(schedule_id)` | sync | „Jetzt ausführen"-Button |
| `_run_schedule(schedule_id)` | async | Job-Body — Ingest + Outcome zurückschreiben |
| `scheduler` | Modul-Singleton | Vom FastAPI-Lifespan importiert |

## 4.19 `system_prompt_store.py` — Globaler System-Prompt *(NEU v2)*

| Symbol | Signatur | Zweck |
|---|---|---|
| `SYSTEM_PROMPT_MAX_CHARS` | Konstante | 8000 Zeichen Hard-Cap |
| `get_system_prompt()` | `() -> str` | DB-Override oder Default |
| `set_system_prompt(text)` | `(str) -> None` | Persistiert oder löscht (leer = revert) |
| `clear_system_prompt()` | `() -> None` | Revert auf in-code Default |
| `has_override()` | `() -> bool` | UI-Flag |

## 4.20 `settings_overrides.py` — Runtime-Konfigurations-Layer *(NEU v2)*

| Symbol | Signatur | Zweck |
|---|---|---|
| `EditableKey` | Dataclass | Metadaten je Schlüssel (Typ, Range, Help, Warning, options_url) |
| `EDITABLE_KEYS` | Liste | 13 Schlüssel (Embedding-Modell, Chat-Modell, Keep-Alive, Chunks, Retrieval, Temperatur, …) |
| `_coerce(meta, raw)` | sync | Typ-Konversion + Validierung |
| `_apply_to_settings(key, value)` | sync | Lebende `settings` mutieren (+ Logging-Reconfig) |
| `apply_overrides()` | sync | Beim Start alle Overrides laden |
| `set_override(key, raw_value)` | sync | UI-Speichern |
| `clear_override(key)` | sync | UI-Reset |
| `has_override(key)` | sync | UI-Flag |
| `overlay_view()` | sync | UI-Build (mit override-Markierung) |

Infrastruktur­schlüssel (`ALLOWED_BASE_PATHS`, `OLLAMA_BASE_URL`, `QDRANT_*`,
`HOST`, `PORT`, `SQLITE_DB_PATH`, `SOFFICE_BIN`) sind **nicht** über das UI editierbar
— sie erfordern einen Service-Neustart.

## 4.21 `openai_compat.py` — OpenAI-Adapter

| Methode | Signatur | Zweck |
|---|---|---|
| `OpenAIRequestError` | Exception | Anfrage nicht mappbar |
| `ResolvedRequest` | Dataclass | tenant, project, question, session_id, model |
| `resolve_openai_request(req)` | `(...) -> ResolvedRequest` | Mapping inkl. `extra_body` und `rag:tenant:project`-Modell-ID |
| `build_openai_response(*, model, answer, sources, session_id)` | `(...) -> OpenAIChatCompletionResponse` | Antwort-Aufbau |

## 4.22 `utils.py` — Hilfsfunktionen

| Funktion | Signatur | Zweck |
|---|---|---|
| `configure_logging()` | `() -> None` | Initialisiert Logging laut `LOG_LEVEL` (inkl. Rotating-File-Handler) |
| `get_logger(name)` | `(str) -> Logger` | Wrapper |
| `new_id()` | `() -> str` | UUID4 |
| `utcnow_iso()` | `() -> str` | ISO-8601-Zeitstempel |
| `deterministic_uuid(*parts)` | `(...) -> str` | UUID5 (für Chunk-Point-IDs) |
| `sha256_file(path, *, chunk_size=1MB)` | `(Path) -> str` | Stream-Hash |
| `file_modified_iso(path)` | `(Path) -> str` | mtime als ISO |
| `chunked(items, n)` | `(...) -> Iterator` | Batch-Iterator |
| `capture_logs_for_job(job_id)` | Context-Manager | NEU v2 — pro-Job-Filehandler (ContextVar-isoliert) |
| `job_log_path(job_id)` | `(str) -> Path` | Pfad zur Job-Logdatei (NEU v2) |

## 4.23 `admin/` — Admin-Web-UI *(NEU v2)*

| Datei | Zweck |
|---|---|
| `routes.py` | Server-Side-Rendered HTML-Seiten (8 Stück, jeweils via Jinja2) |
| `api.py` | 30+ HTMX-Endpunkte unter `/admin/api/*` (JSON / Partials) |
| `middleware.py` | `RequestLogMiddleware` → schreibt jeden Request in `request_logs` |
| `log_stream.py` | Server-Sent-Events Streams für Anwendungs- und Request-Log |
| `templates/` | `base.html`, 8 Seiten, 12 Partials |
| `static/css/` | `bootstrap.min.css` + `admin.css` (Reineke-Overrides) |
| `static/js/` | `bootstrap.bundle.min.js`, `htmx.min.js`, `admin.js` |

Routen-Übersicht:

| Pfad | Inhalt |
|---|---|
| `/admin/` | Übersicht (Health-Kacheln, Bestands-Statistik, `ALLOWED_BASE_PATHS`) |
| `/admin/agenten` | Mandant/Projekt-Baum mit Pipe-Code, Persona-Prompt, Chat-Modell, Reranker |
| `/admin/dokumente` | Filterbare Dokumenten-Tabelle mit Lösch-Button |
| `/admin/ingest` | Zwei-Schritt-Assistent: Scannen → Ingest (mit Dateityp-Filter) |
| `/admin/zeitplan` | Auto-Ingest-Cron-Editor |
| `/admin/jobs` | Ingest-Verlauf inkl. Status, Fehlern, Log-Download |
| `/admin/logs` | API-Audit-Log + Anwendungs-Log mit Live-Tail (SSE) |
| `/admin/konfiguration` | Runtime-Einstellungen, globaler System-Prompt, Pipe-Function-Quelltext |

## 4.24 `connectors/mediawiki/` — MediaWiki-XML-Connector *(NEU v2)*

| Datei | Zweck |
|---|---|
| `service.py` | Orchestriert XML-Parser → Normalizer → Uploads → `IngestionService._index_loaded_document` |
| `xml_importer.py` | Stream-Parser für `<mediawiki>`-Dumps (Pages, Revisionen) |
| `normalizer.py` | Wikitext → Markdown (Links, Tabellen, Listen, Templates entfernt) |
| `uploads.py` | `[[File:…]]`-Auflösung gegen `images/`-Tree (hashed oder flach) |
| `localsettings.py` | Regex-Parser für `$wgServer` / `$wgArticlePath` / `$wgScriptPath` |
| `cli.py` | `inspect-xml` (Probelauf) und `import-xml` Subkommandos |
| `schemas.py` | `MediaWikiPage`, `MediaWikiFileRef`, `MediaWikiWikiConfig`, `NormalizedPage` |
| `errors.py` | `MediaWikiError` plus Spezialisierungen |

Zwei Dokumenten-Typen: `mediawiki_page` (eine Seite, latest Revision) und
`mediawiki_upload` (eine referenzierte Datei). `payload.url` zeigt im Chat-Quellen­block
auf die Wiki-URL und ist anklickbar.

---

# 5 · HTTP-API im Detail

## 5.1 Native Endpunkte

### `GET /health`
Liefert Status-Items (`backend`, `ollama`, `qdrant`, `embedding_model`, `chat_model`,
`reranker`). Pro Item `{name, ok, detail}`. Der Reranker ist eine **weiche Abhängigkeit** —
`ok=true` bei `disabled`, `enabled, lazy` oder `loaded`; `ok=false` nur bei aktivem
Fehler.

### `POST /sources/scan-path`
Body: `{tenant, project, path, recursive?}`.
Antwort enthält `supported`, `unsupported`, `file_types` und (falls erkannt)
`mediawiki_hint`. Indexiert **nichts**.

### `POST /sources/ingest-path`
Body: `{tenant, project, path, recursive?, reindex_changed_only?, include_extensions?}`.
Komplette Pipeline. `include_extensions=null` ⇒ alle Typen; leeres Array ⇒ Dry-Run.
Antwort: `{job_id, indexed_files, skipped_unchanged, failed_files, chunks_created, errors[]}`.

### `POST /sources/mediawiki/import-xml` *(NEU v2)*
Body: `{tenant, project, xml_path, uploads_path?, wiki{base_url, article_path, script_path},
allowed_namespaces[], include_redirects, include_uploads, reindex_changed_only, dry_run}`.
Antwort: detaillierte Zähler — `pages_seen / indexed / skipped_*`,
`files_seen / indexed / skipped_*`, `unresolved_files`, `warnings`, `errors`.
`status` = `"ok"` oder `"partial"`.

### `GET /documents?tenant=…&project=…&include_deleted=false`
Listet alle Dokumente eines Mandanten/Projekts mit Status, Chunk-Anzahl und
`source_type`.

### `POST /documents/reindex-changed`
Wie `ingest-path`, jedoch ohne neue Dateien — nur geänderte werden re-embedded.
Optional `mark_missing_as_deleted=true` markiert verschwundene Dokumente.

### `DELETE /documents/{document_id}`
Löscht alle zugehörigen Qdrant-Punkte und setzt den SQLite-Status auf `deleted`
(Soft-Delete).

### `POST /chat`
Body: `{tenant, project, question, session_id?, top_k?}`.
Antwort: `{answer, sources[], session_id}`. Antwort­fluss siehe Abschnitt 6.2.

### `POST /retrieve` *(NEU v2)*
LLM-frei — nur Top-K-Quellen ohne Generation. Identischer Retrieval-Pfad
(Embedder, Score-Schwelle, Reranker, Synonym-Expansion). Wird vom Eval-Runner
für Recall@K / MRR-Messungen genutzt.

## 5.2 OpenAI-kompatible Endpunkte

### `GET /v1/models`
Listet virtuelle Modelle in der Form `rag:<tenant>:<project>` (oder `rag:default`,
falls keine Dokumente indiziert sind). Optionale Filter `?tenant=` und `?project=`.

### `POST /v1/chat/completions`
Akzeptiert OpenAI-Standard. Tenant/Projekt werden bestimmt aus
1. Inline-Feldern, 2. `extra_body`, 3. Modell-ID `rag:tenant:project`.
Streaming wird abgelehnt (`stream=true` ⇒ Fehler).
Response enthält zusätzlich (nicht-Standard, abwärtskompatibel)
ein strukturiertes `sources[]`-Array und `session_id`.

## 5.3 Admin-API (Auswahl)

Unter `/admin/api/*` (nicht in der OpenAPI-Spec exportiert, dient ausschließlich
dem mitgelieferten Admin-UI):

| Pfad | Zweck |
|---|---|
| `GET /admin/api/health` | Health-Karten-Partial |
| `GET /admin/api/tenants` / `.json` | Mandanten-Baum |
| `GET /admin/api/documents` | Filterbare Dokumenten-Tabelle |
| `DELETE /admin/api/documents/{id}` | Löschen |
| `POST /admin/api/ingest/scan` | Scan-Partial (mit Skip-Statistik) |
| `POST /admin/api/ingest/run` | Ingest starten (mit Live-Progress-Polling) |
| `POST /admin/api/ingest/run-mediawiki` | MediaWiki-Import starten |
| `GET /admin/api/jobs` / `/{id}/progress` / `/{id}/logs` / `/{id}/logs.txt` | Ingest-Verlauf |
| `GET /admin/api/schedules` · `POST` · `DELETE /{id}` · `POST /{id}/run-now` | Auto-Ingest-Zeitpläne |
| `GET /admin/api/request-logs` / `/stream` | Request-Audit (HTML + SSE) |
| `GET /admin/api/app-log/stream` | Anwendungs-Log (SSE) |
| `GET /admin/api/config` · `POST /{key}` · `POST /{key}/reset` | Runtime-Settings |
| `GET /admin/api/system-prompt` · `POST` · `DELETE` | Globaler System-Prompt |
| `POST /admin/api/agents/{tenant}/{project}/prompt` | Persona-Prompt |
| `POST /admin/api/agents/{tenant}/{project}/chat-model` | Per-Agent-Chat-Modell |
| `POST /admin/api/agents/{tenant}/{project}/rerank` | Per-Agent-Reranker |
| `GET /admin/api/agents/{tenant}/{project}/prompt-preview` | Zusammengesetzter System-Prompt |
| `GET /admin/api/openwebui-pipe.py` | Vorkonfigurierte Pipe-Function (mit `?tenant=&project=` substituiert) |
| `GET /admin/api/ollama/models?role=chat` / `?role=embedding` | Verfügbare Ollama-Modelle |

## 5.4 Fehler-Klassen

| HTTP | Auslöser |
|---|---|
| 400 | `PathSecurityError`, `OpenAIRequestError`, `MediaWikiError`, `ValueError` |
| 404 | Dokument / Schedule nicht gefunden |
| 500 | `QdrantStoreError`, allgemeiner Fehler |
| 502 | `OllamaError` (LLM nicht erreichbar) |

---

# 6 · Datenflüsse

## 6.1 Ingest-Pipeline (Datei­system)

1. `POST /sources/ingest-path` → Pfad-Sicherheits-Gate.
2. `IngestionService.ingest_path()` öffnet Qdrant-Collection (Dimension passend zum Embedding-Modell).
3. `scan_directory()` liefert alle unterstützten Dateien (inkl. MediaWiki-Hint).
4. Optionaler `include_extensions`-Filter narrowt die Arbeitsmenge.
5. Pro Datei (in der Schleife, mit live aktualisiertem `IngestionJob.current_file`):
   * `sha256_file()` berechnet Prüfsumme.
   * Bei unverändertem Hash + Status `indexed` → Skip.
   * `load_document()` extrahiert Segmente (PDF, DOCX, XLSX, PPTX, ODT, HTML, …).
   * `chunk_document()` erzeugt Chunks (Text- oder Tabellen-Strategie),
     jeder Chunk bekommt das „[Datei: stem]"-Präfix.
   * `OllamaClient.embed_many()` liefert Vektoren.
   * `QdrantStore.upsert_chunks()` schreibt Punkte mit Tenant/Projekt-Payload.
   * SQLite-Status auf `indexed` (oder `failed`/`requires_ocr`/`empty`).
   * `_update_job_progress()` schreibt laufende Zähler.
6. Antwort enthält Job-Statistik und Fehlerliste. Der Job-Log liegt unter
   `storage/job-logs/<job_id>.log` und ist im UI tailbar.

## 6.2 Chat-Pipeline

1. `POST /chat` (oder `/v1/chat/completions`).
2. `_resolve_session_id()` lädt oder legt eine `ChatSession` an (DB-Phase A, kurzlebig).
3. **Meta-Frage-Abfang:** Falls die Frage nach Mengen­angaben („wie viele Dokumente?")
   aussieht ⇒ echter `SELECT COUNT(*)` aus SQLite, sofort Antwort, kein LLM-Aufruf.
4. `_load_recent_citation_ids()` holt Qdrant-IDs der letzten drei zitierten Antworten
   dieser Session (Cross-Turn-Recall).
5. `RetrievalService.retrieve()`:
   * `_expand_query_with_synonyms()` ergänzt DACH-Synonyme.
   * `OllamaClient.embed()` erzeugt Frage-Embedding.
   * `QdrantStore.search()` mit erzwungenem Tenant/Projekt-Filter + Score-Schwelle
     liefert Overfetch-Kandidaten (`top_k * 2` oder `overfetch_k`).
   * Cross-Turn-Recall-Punkte werden in den Pool gemerged.
   * Falls Reranker aktiv (Per-Agent-Override oder Auto): Cross-Encoder reordert,
     der Score ersetzt den Bi-Encoder-Score.
   * `_dedup_by_stem()` faltet PDF/DOCX-Dubletten zusammen, dann `[:top_k]`.
6. Bei **0 Treffern** → `NO_CONTEXT_ANSWER`, Persistierung, Rückgabe.
7. Sonst: `_build_context()` erzeugt nummerierten Quellen­block,
   `_compose_system_prompt()` baut System-Prompt + Persona,
   `_load_recent_messages()` lädt bis zu `CHAT_HISTORY_TURNS`-Paare,
   `OllamaClient.chat()` generiert Antwort (mit per-Agent-Chat-Modell, falls gesetzt).
8. `_strip_llm_sources_trailer()` entfernt halluzinierte „Quellen:"-Listen,
   `_ensure_sources_appended()` hängt unseren deterministischen Quellen­block an.
9. `_persist_messages()` speichert User- und Assistant-Message inkl. `sources_json`.
10. Antwort: `{answer, sources, session_id}`. `sources[].url` enthält bei
    MediaWiki-Quellen die Wiki-URL.

## 6.3 Auto-Ingest-Scheduler *(NEU v2)*

1. FastAPI-Lifespan ruft `scheduler.start()` → `AsyncIOScheduler` läuft im Process.
2. `reload_schedules()` liest alle `IngestSchedule`-Zeilen, registriert je aktivem
   Eintrag einen `CronTrigger(hour, minute, second=0, timezone='UTC')`.
3. Beim Trigger ruft `_run_schedule(schedule_id)` `IngestionService.ingest_path()`
   und schreibt `last_run_at`, `last_status`, `last_indexed/skipped/failed/chunks`,
   `last_job_id` zurück in die Schedule-Zeile.
4. Admin-UI „Jetzt ausführen" ruft `trigger_now()` → Einmal-Job für sofortigen Lauf.
5. CRUD über die `/admin/api/schedules`-Endpunkte ruft jeweils `reload_schedules()`,
   sodass die In-Memory-Jobs konsistent mit der DB bleiben.

## 6.4 MediaWiki-Import *(NEU v2)*

1. `POST /sources/mediawiki/import-xml` → Pfad-Sicherheits-Gate für
   `xml_path` und `uploads_path`.
2. `MediaWikiImportService.import_xml()`:
   * `iter_pages()` stream-parst den XML-Dump, applies Namespace- und Redirect-Filter.
   * `normalize_wikitext()` macht aus Wikitext lesbares Markdown,
     extrahiert Kategorien und `[[File:…]]`-Referenzen.
   * Für jede Seite: `Document` (Typ `mediawiki_page`) mit
     `source_metadata_json` (page_id, revision_id, categories, linked_files, page_url),
     `_index_loaded_document()` mit `payload_extras={url: wiki_url, source_type: ...}`.
   * Zweiter Pass: jedes referenzierte Upload wird via `resolve_upload()` aufgelöst
     (hashed `a/ab/…` oder flach) und mit dem normalen Datei-Loader ingestet.
3. Antwort enthält detaillierte Zähler und Listen `unresolved_files`, `warnings`, `errors`.

## 6.5 Externe Service-Abhängigkeiten

| Quelle | Ziel | Protokoll | Zweck |
|---|---|---|---|
| Backend | Ollama (`:11434`) | HTTP | Embeddings, Chat, `/api/show` (num_ctx-Probe) |
| Backend | Qdrant (`:6333`) | HTTP | Upsert, Suche, Retrieve, Indexes |
| Backend | Datei­system | POSIX | Originaldokumente lesen, Konvertierungen schreiben |
| Backend | LibreOffice (Subprozess) | CLI | Legacy-Konvertierung (`.doc` / `.xls` / `.odt`) |
| Backend | FlagEmbedding / PyTorch | In-Process | Reranker (lazy, lokal) |
| Browser | Backend `/admin/api/*/stream` | SSE | Live-Tail Anwendungs- und Request-Log |

---

# 7 · Datenmodell

## 7.1 SQLite-Schema (10 Tabellen)

| Tabelle | Schlüssel | Zweck |
|---|---|---|
| `file_sources` | id | Konfigurierte Wurzel­pfade pro Mandant |
| `documents` | id (UNIQUE: tenant, project, source_path) | Indizierte Dateien (+ `source_type`, `source_metadata_json`) |
| `ingestion_jobs` | id | Audit-Trail aller Ingest-Läufe (+ `current_file`) |
| `chat_sessions` | id | Konversations­container |
| `chat_messages` | id (FK: session_id) | Einzelne Nachrichten + Quellen |
| `request_logs` *(NEU)* | id | HTTP-Audit für `/admin/logs` |
| `ingest_schedules` *(NEU)* | id | Wiederkehrender Auto-Ingest pro Agent |
| `tenant_project_prompts` *(NEU)* | PK (tenant, project) | Persona, Chat-Modell-Override, Reranker-Override |
| `system_prompt_overrides` *(NEU)* | id (`'global'`) | Globaler System-Prompt-Override |
| `settings_overrides` *(NEU)* | key | Runtime-überschriebene Settings |

WAL-Mode, `foreign_keys=ON`, `synchronous=NORMAL`, `busy_timeout=30 s`.

Indexes über `tenant`, `project`, `status`, `created_at + status_code` (für die
Request-Log-Filter im UI), plus der zusammengesetzte UNIQUE-Index
`(tenant, project, source_path)` auf `documents`.

## 7.2 Qdrant-Punktformat

```
{
  id      : UUID5(document_id, chunk_index),
  vector  : [float × dim],
  payload : {
      tenant, project, document_id, file_name,
      file_extension, source_path, checksum, modified_at, created_at,
      document_type, chunk_index, page?, sheet?,
      row_start?, row_end?, text,
      source_type,           // filesystem | mediawiki_page | mediawiki_upload
      url?,                  // wiki click-through, optional (NEU v2)
      page_id?, revision_id? // MediaWiki-Provenance (NEU v2)
  }
}
```

Indexierte Payload-Felder: `tenant`, `project`, `document_id`, `file_extension`.

---

# 8 · Konfiguration (`.env`)

| Variable | Default | Beschreibung |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | LLM-Endpunkt |
| `OLLAMA_KEEP_ALIVE` | `1h` | NEU v2 — `5m`/`24h`/`-1` (forever)/`0` |
| `OLLAMA_NUM_CTX` | *(unset, auto)* | NEU v2 — manueller Override des KV-Cache-Fensters |
| `QDRANT_URL` | `http://localhost:6333` | Vektor-DB |
| `QDRANT_API_KEY` | — | Optional |
| `QDRANT_COLLECTION` | `documents` | Collection-Name |
| `EMBEDDING_MODEL` | `mxbai-embed-large` | Empfohlen: `bge-m3` |
| `CHAT_MODEL` | `qwen2.5:14b` | Globaler Fallback (per-Agent-Override im UI) |
| `RERANK_ENABLED` | `true` | NEU v2 — globaler Killswitch |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | NEU v2 — Standard-Reranker |
| `RERANK_OVERFETCH_K` | `20` | NEU v2 — Mindest-Kandidaten­pool |
| `RERANK_AUTO_ENABLE_MIN_DOCS` | `100` | NEU v2 — Auto-Aktivierungs-Schwelle |
| `ALLOWED_BASE_PATHS` | — | **Pflicht** — komma­separierte absolute Pfade |
| `SQLITE_DB_PATH` | `./storage/rag.sqlite` | Metadaten-DB |
| `CHUNK_SIZE` | `1000` | Zeichen pro Text-Chunk |
| `CHUNK_OVERLAP` | `150` | Überlappung |
| `XLSX_ROWS_PER_CHUNK` | `40` | Zeilen pro Tabellen-Chunk |
| `XLSX_MAX_CHARS_PER_CHUNK` | `6000` | Hardlimit |
| `RETRIEVAL_TOP_K` | `6` | Anzahl Treffer |
| `MIN_RETRIEVAL_SCORE` | `0.35` | Schwelle für „relevant" |
| `CHAT_TEMPERATURE` | `0.1` | Sampling |
| `CHAT_MAX_TOKENS` | `1024` | Max. Antwort­länge |
| `CHAT_HISTORY_TURNS` | `6` | NEU v2 — Anzahl früherer Frage-/Antwort-Paare im LLM-Kontext |
| `SOFFICE_BIN` | `soffice` | LibreOffice-Binary |
| `HOST` | `0.0.0.0` | FastAPI |
| `PORT` | `8000` | FastAPI |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARN/ERROR |

13 dieser Schlüssel sind zusätzlich über das Admin-UI ohne Neustart änderbar
(siehe `settings_overrides.EDITABLE_KEYS`). Infrastruktur- und Sicherheits­schlüssel
sind bewusst nicht editierbar.

---

# 9 · Sicherheits-Eigenschaften

1. **Allow-list-basierter Datei­zugriff.** Ohne `ALLOWED_BASE_PATHS` lehnt das System
   jede Scan- oder Ingest-Anfrage ab. Gilt auch für den MediaWiki-Connector
   (`xml_path` und `uploads_path` werden vor dem Lesen validiert).
2. **Pfad-Traversal-Schutz.** `resolve_safe_path()` kanonisiert mit `Path.resolve()`,
   prüft Eltern-Beziehung, lehnt System­verzeichnisse (`/etc`, `/root`, …) ab,
   sofern diese nicht explizit freigegeben sind.
3. **Mandanten-Isolation als Pflicht-Filter.** `QdrantStore.search()` und
   `get_points_by_ids()` werfen `QdrantStoreError`, wenn `tenant` oder
   `project` leer sind — eine ungefilterte Vektor-Suche ist nicht möglich.
4. **Anti-Halluzination.** Ohne Treffer über `MIN_RETRIEVAL_SCORE` gibt das
   System die Konstante `NO_CONTEXT_ANSWER` zurück; das LLM wird in diesem
   Fall **nicht** angefragt. Meta-Fragen werden direkt aus SQLite beantwortet.
   LLM-erfundene „Quellen:"-Trailer werden deterministisch entfernt und durch
   den realen Quellen­block ersetzt.
5. **Quellen­zwang.** Jede generierte Antwort wird mit `sources` (Datei,
   Seite/Sheet/Zeilenbereich, Score, plus `source_type` und `url` für Wiki-Quellen)
   ausgeliefert und persistiert.
6. **Offline-Betrieb.** Sämtliche Modelle laufen lokal über Ollama bzw. lokal
   geladene Reranker-Gewichte; das Backend macht keine ausgehenden Verbindungen
   außer zu Qdrant und Ollama.
7. **Read-only auf Originale.** Ingest-Pipeline schreibt nicht in die
   Quell­verzeichnisse. Konvertierungs­ergebnisse landen in `storage/converted/`.
8. **Junk-Filter.** Office-Lock-Dateien (`~$…`), macOS-Resource-Forks (`._…`),
   sonstige Dotfiles und Zero-Byte-Stubs werden beim Scan ignoriert, bevor sie
   die Loader erreichen.
9. **Audit-Trail.** Jede HTTP-Anfrage landet (außer SSE-Streams) in `request_logs`
   mit Methode, Pfad, Statuscode, Dauer, Tenant/Project und Client-IP.
10. **Session-zentrierte DB-Schreibphasen.** Während der LLM-Generation hält
    ChatService keine SQLite-Connection — das schützt den Connection-Pool
    unter Last und verhindert WAL-Blockaden.

---

# 10 · Tests

Test-Verzeichnis: `backend/tests/` (25 Module + Eval-Unterverzeichnis).
Auführung:

```
cd backend
pytest -v
```

| Test­datei | Fokus |
|---|---|
| `test_path_security.py` | Allow-list, Traversal, System-Pfad-Ablehnung |
| `test_source_scanner.py` | Klassifikation, Recursive-Flag, Junk-Filter |
| `test_scanner_mediawiki_detect.py` | MediaWiki-Export-Erkennung (NEU v2) |
| `test_extractors.py` | PDF/DOCX/XLSX/HTML-Parsing |
| `test_pptx_loader.py` | PPTX-Slides + Speaker Notes (NEU v2) |
| `test_chunker_xlsx.py` | XLSX-Chunking inklusive Header-Wiederholung |
| `test_chunker_filename.py` | Filename-Prefix in jedem Chunk (NEU v2) |
| `test_qdrant_filter.py` | Pflicht-Filter Tenant + Projekt |
| `test_openai_compat.py` | OpenAI-Request-Auflösung |
| `test_chat_no_hits.py` | Fallback bei fehlenden Treffern (async) |
| `test_chat_session_lifecycle.py` | Session-IDs, Tenant/Projekt-Cross-Check (NEU v2) |
| `test_chat_cross_turn_recall.py` | Past-Citation-IDs (NEU v2) |
| `test_chat_meta_question.py` | „wie viele Dokumente?" (NEU v2) |
| `test_chat_sources_trailer.py` | Halluzinierte Quellen entfernen (NEU v2) |
| `test_retrieve_endpoint.py` | LLM-freier Retrieve-Endpunkt (NEU v2) |
| `test_retrieval_rerank.py` | Reranker-Pfad inkl. Fallback (NEU v2) |
| `test_retrieval_dedup.py` | Stem-Dedup (NEU v2) |
| `test_rerank_settings.py` | Drei-Schichten-Resolver (NEU v2) |
| `test_query_synonym_expansion.py` | Dienstleister ↔ Lieferant (NEU v2) |
| `test_ollama_num_ctx.py` | `/api/show`-Probe + Cache (NEU v2) |
| `test_settings_overrides.py` | Runtime-Editieren, Coercion (NEU v2) |
| `test_system_prompt_override.py` | DB-Override + Revert (NEU v2) |
| `test_persona_prompt.py` | Persona je Mandant/Projekt (NEU v2) |
| `test_ingest_progress.py` | `current_file`, Prozent, ETA (NEU v2) |
| `test_job_logs.py` | Pro-Job-Logging (NEU v2) |
| `connectors/mediawiki/…` | XML, Normalizer, Uploads, Service, Integration |
| `eval/` | Versionierter Fragenkatalog (Recall@5 / MRR / Faithfulness / Latenz) |

CI führt die Suite auf jedem PR aus (Python 3.11 + 3.12, GitHub Actions).

---

# 11 · Admin-Web-UI

Das Admin-UI ist Server-Side-Rendered (Jinja2) und nutzt **htmx** für partielle
Updates — keine SPA, kein Build-Schritt, kein npm. Statische Assets
(Bootstrap, htmx) liegen direkt im Repository unter `backend/app/admin/static/`.

| Seite | Routine im Backend | Wichtigste Inhalte |
|---|---|---|
| Übersicht | `routes.page_overview()` | Health-Karten (alle 15 s), Doc-/Chunk-/Agent-Zähler, Allow-list |
| Agenten | `routes.page_agents()` | Mandant→Projekt-Baum, Pipe-Function-Quelltext, Persona-Prompt-Editor, Chat-Modell-Picker, Reranker-Tri-State |
| Dokumente | `routes.page_documents()` | Filterbare Tabelle (Mandant/Projekt/Status/Suche), Lösch-Button |
| Ingest-Assistent | `routes.page_ingest()` | Zwei-Schritt-Wizard: Scannen → Dateityp-Auswahl → Ingest (mit Live-Progress) |
| Zeitplan | `routes.page_schedules()` | Cron-Editor (HH:MM UTC), aktive/pausierte Einträge, „Jetzt ausführen" |
| Ingest-Verlauf | `routes.page_jobs()` | Letzte 200 Läufe mit Status, Fehler­indikator, Log-Download |
| Logs | `routes.page_logs()` | API-Audit (Filter: Status/Methode/Mandant) und Anwendungs-Log mit Live-Tail (SSE) |
| Konfiguration | `routes.page_config()` | Runtime-Settings, Read-only-Infra-Felder, globaler System-Prompt, Pipe-Function-Quelltext zum Kopieren |

Die UI nutzt durchweg **deutsche Beschriftungen**. Das `data-bs-theme="dark"`
gilt nur für die Navbar; Inhalts­bereiche sind im Light-Modus.

Sicherheit: Das UI ist **nicht** durch Authentifizierung geschützt. Es lebt
im Vertrauens­modell „intern erreichbar, Reverse-Proxy macht TLS und ggf. Auth".
Für externe Exposition muss vor dem Backend ein Auth-Proxy stehen (zukünftiger
OIDC-Support ist im Installer-README erwähnt).

---

# 12 · Bedienung über die HTTP-API

## 12.1 Voraussetzungen

* Ollama ≥ 0.4 lokal laufend (Modelle vorgepullt: `bge-m3`, `qwen2.5:14b/32b` o. ä.).
* Qdrant ≥ 1.12 erreichbar.
* LibreOffice (für `.doc`/`.xls`/`.odt`) — optional, falls Legacy-Formate vorkommen.
* Reranker-Dependencies (PyTorch via FlagEmbedding) sind im Image enthalten,
  belegen Disk aber nicht VRAM, solange `RERANK_ENABLED=false`.

## 12.2 Schnellstart

```bash
git clone <repo>
cd rag-qdrant-local
cp .env.example .env
# In .env: ALLOWED_BASE_PATHS=/srv/dokumente,/mnt/policies   ← anpassen

cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Anschließend ist das Admin-UI unter `http://localhost:8000/admin/` erreichbar
(siehe Handbuch). API-Dokumentation: `http://localhost:8000/docs`.

## 12.3 Beispiel-Workflow (CLI)

```
# 1. Health-Check
curl -s http://localhost:8000/health | jq

# 2. Scan (zeigt Dateitypen ohne zu indexieren)
curl -X POST http://localhost:8000/sources/scan-path \
  -H "Content-Type: application/json" \
  -d '{"tenant":"reineke","project":"watch","path":"/srv/dokumente/watch"}'

# 3. Ingest (komplett, oder nur ausgewählte Endungen)
curl -X POST http://localhost:8000/sources/ingest-path \
  -H "Content-Type: application/json" \
  -d '{
    "tenant":"reineke","project":"watch",
    "path":"/srv/dokumente/watch",
    "include_extensions":[".pdf",".docx"]
  }'

# 4. Frage stellen
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant":"reineke","project":"watch",
    "question":"Welche Passwortregeln gelten?"
  }'

# 5. LLM-freier Retrieve (nur Quellen, für Eval-Runs)
curl -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "tenant":"reineke","project":"watch",
    "question":"Welche Passwortregeln gelten?","top_k":5
  }'
```

## 12.4 Open-WebUI-Integration

* In Open WebUI als „OpenAI-API" eintragen, Base-URL = `http://<backend>:8000/v1`.
* API-Key beliebig (wird nicht geprüft).
* Modell wählen: `rag:reineke:watch` (Format `rag:<tenant>:<project>`).
* Streaming **aus** (`stream=true` wird abgelehnt).
* Alternativ die im Admin-UI angebotene Pipe-Function nutzen — sie ist
  pro `(tenant, project)`-Paar vorbefüllt und kopierbar.

## 12.5 Wartung

| Aktion | Befehl |
|---|---|
| Reindex geänderter Dateien | `POST /documents/reindex-changed` |
| Verschwundene Dateien als „deleted" | `…?mark_missing_as_deleted=true` |
| Einzelnes Dokument entfernen | `DELETE /documents/{id}` (auch im UI per Klick) |
| SQLite-Backup | `sqlite3 storage/rag.sqlite ".backup backup.sqlite"` |
| Qdrant-Snapshot | `curl -X POST http://localhost:6333/collections/documents/snapshots` |
| Auto-Ingest pausieren | UI: Zeitplan → Eintrag deaktivieren |
| Settings ohne Neustart anpassen | UI: Konfiguration → Wert ändern → „↳" |

---

# 13 · Auslieferung & Installer-Bundle

Reineke-RAG wird als versioniertes Installer-Bundle ausgeliefert
(`scripts/build-bundle.sh`):

```
reineke-rag-installer-<version>.tar.gz
├── README.md             # Installer-Anleitung
├── install.sh            # Haupt-Installer (Docker Compose + systemd)
├── uninstall.sh
├── docker-compose.yml    # Qdrant + Ollama + Backend
├── .env.example
├── images/               # (offline) Container-Tarballs
├── models/               # (offline) Ollama-Modell-Blobs
├── scripts/              # wait-for / backup / restore
├── systemd/              # reineke-rag.service + Backup-Timer
└── VERSION
```

Der Installer:

1. prüft Voraussetzungen (Docker, Compose v2),
2. legt `/opt/reineke-rag/` an,
3. lädt Images (offline aus `images/` oder online),
4. startet Qdrant + Ollama,
5. pullt / lädt KI-Modelle (`bge-m3`, Chat-Modell, optional Reranker),
6. startet das Backend, ruft `GET /health` ab,
7. installiert systemd-Units (Reboot-fest),
8. richtet täglichen Backup-Timer ein (03:00, Aufbewahrung 14 Tage).

Apple-Silicon-Spezialfall: Ollama läuft **nativ** auf dem Host
(deutlich schneller als die Linux-VM-Variante); der Installer wird
mit `--skip-ollama` aufgerufen.

---

# 14 · Status & nächste Schritte

* **Reife.** End-to-End funktionsfähig; alle 25 Test-Module grün.
  Retrieval-Quality-Eval (`tests/eval/`) liefert Recall@5 = 11/11
  (Stand 2026-05-20, Korpus 62 Dokumente).
* **Empfohlene Konfiguration für DACH-Kunden.**
    * Embedder: `bge-m3` (Default in `.env.example` bereits gesetzt).
    * Chat: `qwen2.5:32b-instruct-q4_K_M` (Qualität) oder `qwen2.5:14b` (Throughput).
    * Reranker: globalen Killswitch auf `true`, Smart-Default aktivieren —
      pro Kollektion ab 100 Dokumenten automatisch an.
* **Skalierung.** SQLite-Setup (Busy-Timeout 30 s, WAL) ist für moderate
  Parallel­last (≤ 10 gleichzeitige Chats) ausgelegt; bei höherer Last
  Migration auf PostgreSQL.
* **Offene Themen.**
    * OIDC / SSO vor das Admin-UI schalten.
    * MediaWiki SQL-Mode (`POST /sources/mediawiki/import-sql`) — Design vorhanden,
      teilt die Service-Schicht mit dem XML-Mode.
    * OCR-Pipeline für reine Bild-PDFs (heute werden sie als `requires_ocr` markiert).

---

*Diese Dokumentation wurde aus der Code­basis `rag-qdrant-local/` (rund 5.300 Python-Zeilen,
23 Module, 12 öffentliche HTTP-Endpunkte plus ein Admin-API-Submodul mit über 30
HTMX-Endpunkten) erzeugt. Für Detail­fragen siehe Inline-Docstrings, das
Handbuch (`HANDBUCH.pdf`) und `TEST_REPORT.md`.*
