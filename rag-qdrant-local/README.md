# rag-qdrant-local

Lokales, offline lauffähiges RAG-System (Retrieval-Augmented Generation) auf
Basis von **FastAPI**, **Ollama** und **Qdrant**. Dokumente werden direkt
aus serverseitig gemounteten Verzeichnissen indexiert — Upload ist möglich,
aber nicht der primäre Weg.

Unterstützte Dateitypen: `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.html`, `.htm`.

---

## Voraussetzungen

Folgende Dienste werden als **bereits laufend** angenommen:

| Dienst       | Endpoint (Default)         | Bemerkung                              |
| ------------ | -------------------------- | -------------------------------------- |
| Ollama       | `http://localhost:11434`   | Embedding- und Chat-Modell installiert |
| Qdrant       | `http://localhost:6333`    | REST-Port erreichbar                   |
| OpenWebUI    | `https://localhost`        | Optional — kann später `/chat` rufen   |

Optional, nur für Legacy-Dokumente (`.doc`, `.xls`):

```bash
# macOS
brew install --cask libreoffice

# Debian/Ubuntu
sudo apt-get install libreoffice
```

Das Backend prüft beim Start, ob `soffice` im `PATH` liegt — falls nicht,
werden `.doc`/`.xls` mit einer klaren Fehlermeldung abgelehnt, alle anderen
Formate funktionieren weiter.

Modelle ggf. ziehen:

```bash
ollama pull mxbai-embed-large
ollama pull qwen2.5:14b
```

---

## Schnellstart

```bash
git clone <this-repo> rag-qdrant-local
cd rag-qdrant-local

cp .env.example .env
# .env editieren — vor allem ALLOWED_BASE_PATHS!

cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Healthcheck:

```bash
curl -s http://localhost:8000/health | jq
```

---

## Konfiguration (`.env`)

Alle Werte können in `.env` (im Projekt-Root) gesetzt werden. Siehe
[`.env.example`](./.env.example) für die vollständige Liste.

### Wichtig: `ALLOWED_BASE_PATHS`

Komma-separierte, **absolute** Pfade. Nutzer können nur Dateien indexieren,
die physisch unter einem dieser Pfade liegen. Das System wehrt
Path-Traversal (`..`) und Zugriff auf Systemverzeichnisse (`/etc`, `/root`,
`/home`, `/var`, …) aktiv ab.

```env
ALLOWED_BASE_PATHS=/mnt/rag-data,/srv/customer-files
```

> Wenn `ALLOWED_BASE_PATHS` leer ist, lehnt das Backend jede Scan- oder
> Ingest-Anfrage ab.

### Modelle und Chunking

```env
EMBEDDING_MODEL=mxbai-embed-large
CHAT_MODEL=qwen2.5:14b

CHUNK_SIZE=1000
CHUNK_OVERLAP=150
XLSX_ROWS_PER_CHUNK=40

RETRIEVAL_TOP_K=6
MIN_RETRIEVAL_SCORE=0.35
```

> Beim ersten Start ermittelt das Backend die Embedding-Dimension durch
> einen Test-Aufruf gegen Ollama und legt die Qdrant-Collection passend an.
> Wechselst du später das Embedding-Modell, lege die Collection neu an
> (siehe Troubleshooting).

---

## API-Endpunkte

### `GET /health`

Prüft Backend, Qdrant, Ollama und ob beide Modelle gezogen sind.

```bash
curl -s http://localhost:8000/health | jq
```

### `POST /sources/scan-path`

Scannt einen erlaubten Pfad **ohne** zu indexieren.

```bash
curl -s -X POST http://localhost:8000/sources/scan-path \
  -H "Content-Type: application/json" \
  -d '{
    "tenant": "mk-lasertechnik",
    "project": "jonyx-analyse",
    "path": "/mnt/rag-data/mk-lasertechnik/jonyx",
    "recursive": true
  }' | jq
```

### `POST /sources/ingest-path`

Hasht jede Datei, vergleicht mit SQLite, ingestiert nur Geändertes.

```bash
curl -s -X POST http://localhost:8000/sources/ingest-path \
  -H "Content-Type: application/json" \
  -d '{
    "tenant": "mk-lasertechnik",
    "project": "jonyx-analyse",
    "path": "/mnt/rag-data/mk-lasertechnik/jonyx",
    "recursive": true,
    "reindex_changed_only": true
  }' | jq
```

### `GET /documents?tenant=…&project=…`

Liefert die in SQLite registrierten Dokumente.

### `POST /documents/reindex-changed`

Wie Ingest, aber kann zusätzlich Dateien, die auf Disk verschwunden sind,
als `deleted` markieren (`mark_missing_as_deleted: true`).

### `DELETE /documents/{document_id}`

Markiert das Dokument als `deleted` und entfernt alle zugehörigen Punkte
aus Qdrant.

### `POST /chat`

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant": "mk-lasertechnik",
    "project": "jonyx-analyse",
    "question": "Welche Server werden in den Dokumenten genannt?",
    "session_id": null
  }' | jq
```

Antwort enthält `answer`, `sources` und `session_id`. Antworten **ohne
Treffer** lauten immer:

> *Das steht nicht eindeutig in den bereitgestellten Dokumenten.*

### OpenAI-kompatibel: `POST /v1/chat/completions` und `GET /v1/models`

Für OpenWebUI und andere OpenAI-Clients — siehe Abschnitt
[OpenWebUI-Integration](#openwebui-integration) weiter unten.

---

## OpenWebUI-Integration

Vollständige Anleitung inkl. fertiger Pipe-Function:
**[docs/OPENWEBUI_INTEGRATION.md](docs/OPENWEBUI_INTEGRATION.md)** —
referenziert [docs/openwebui_pipe.py](docs/openwebui_pipe.py) als
Copy-&-Paste-Quelle.

Kurzfassung: Das Backend bietet zwei Wege für OpenWebUI: einen einfachen Webhook und
— empfohlen — einen **OpenAI-kompatiblen Endpoint** unter `/v1`.

### Empfohlen: OpenAI-kompatibler Endpoint

In OpenWebUI:

1. **Settings → Connections → OpenAI API** (oder *Models → Manage Connections*).
2. **Base URL:** `http://localhost:8000/v1`
3. **API Key:** beliebiger Wert (z. B. `not-needed`) — das Backend prüft ihn nicht.
4. Speichern. Im Modell-Picker erscheint dann pro indexiertem
   `(tenant, project)`-Paar ein virtuelles Modell mit dem Namen
   `rag:<tenant>:<project>` (z. B. `rag:mk-lasertechnik:jonyx-analyse`).

> Solange noch nichts indexiert wurde, wird ein generisches `rag:default`
> ausgeliefert. Sobald du das erste Mal `/sources/ingest-path` aufrufst,
> erscheinen die echten Modelle.

#### Wie tenant + project ermittelt werden

Reihenfolge der Auflösung:

1. **Top-Level-Felder** im Request-Body: `"tenant"` und `"project"`
   (nicht-Standard, aber unterstützt).
2. **`extra_body`** im Request:
   ```json
   { "extra_body": { "tenant": "mk-lasertechnik", "project": "jonyx-analyse" } }
   ```
3. **Encoded im Model-Namen**: `model: "rag:mk-lasertechnik:jonyx-analyse"` —
   so funktioniert die Auswahl aus dem OpenWebUI-Modell-Picker out of the box.

#### Beispiel-Request

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "rag:mk-lasertechnik:jonyx-analyse",
    "stream": false,
    "messages": [
      {"role": "user", "content": "Welche Server werden in den Dokumenten genannt?"}
    ]
  }' | jq
```

Antwort (gekürzt):

```json
{
  "id": "chatcmpl-…",
  "object": "chat.completion",
  "created": 1730000000,
  "model": "rag:mk-lasertechnik:jonyx-analyse",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Laut der Asset-Liste …\n\nQuellen:\n- assetliste.xlsx, Sheet \"Server\", Zeilen 2-25, Chunk 0"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0 },
  "sources": [
    {
      "file_name": "assetliste.xlsx",
      "document_id": "…",
      "sheet": "Server",
      "row_start": 2,
      "row_end": 25,
      "chunk_index": 0,
      "score": 0.82
    }
  ],
  "session_id": "…"
}
```

`choices[0].message.content` enthält die Antwort inklusive `Quellen:`-Block,
sodass OpenWebUI sie unverändert rendern kann. Zusätzlich liefert das
Backend ein **strukturiertes** `sources`-Array (nicht-Standard, aber
abwärtskompatibel — OpenAI-Clients ignorieren unbekannte Felder).

#### Einschränkungen des MVP

* `stream=true` wird abgelehnt (`400 Bad Request`). Setze in OpenWebUI
  unter *Advanced Settings* das Streaming für dieses Modell auf `false`.
* Tools / Functions / `n > 1` / `logprobs` werden ignoriert.
* `usage` wird mit Nullen ausgeliefert — der Token-Count ist für Ollama
  schwer exakt zu bestimmen.

### Alternative: einfacher Webhook auf `/chat`

Wenn du keine OpenAI-Kompatibilität brauchst (z. B. eigene Pipeline /
HTTP-Action):

* **URL:** `http://<backend-host>:8000/chat`
* **Methode:** `POST`
* **Header:** `Content-Type: application/json`
* **Body:**

```json
{
  "tenant": "mk-lasertechnik",
  "project": "jonyx-analyse",
  "question": "{{user_message}}",
  "session_id": "{{conversation_id}}"
}
```

---

## Datei-Verarbeitung im Detail

| Format  | Verarbeitung                                                         |
| ------- | -------------------------------------------------------------------- |
| `.pdf`  | `pypdf`, seitenweise Text. Ohne Text → `requires_ocr` (kein OCR-MVP) |
| `.docx` | `python-docx`; Tabellen werden als Markdown extrahiert               |
| `.doc`  | LibreOffice `--convert-to docx` → wie `.docx`                        |
| `.xlsx` | `openpyxl`; Header erkannt, Chunks à `XLSX_ROWS_PER_CHUNK` Zeilen    |
| `.xls`  | LibreOffice `--convert-to xlsx` → wie `.xlsx`                        |

PDFs / DOCXs werden mit `CHUNK_SIZE` Zeichen und `CHUNK_OVERLAP` Zeichen
Overlap auf Absatz-/Satz-Grenzen geschnitten. XLSX wird **nicht** nach
Zeichen, sondern nach Zeilenblöcken gechunkt; die erkannte Headerzeile
wird in jedem Chunk wiederholt.

---

## Sicherheits-Features

* **Allow-list** der erlaubten Wurzelpfade.
* **Path-Traversal-Schutz**: Eingabe muss absolut sein, wird via `Path.resolve()`
  kanonisiert und gegen die Allow-list und eine System-Deny-list geprüft.
* **Tenant/Project-Isolation**: jede Qdrant-Suche enthält **zwingend**
  `tenant`- und `project`-Filter. `QdrantStore.search()` weigert sich, ohne
  diese aufgerufen zu werden.
* **Keine Antwort ohne Quellen**: Findet das System keine relevanten Chunks
  (Score < `MIN_RETRIEVAL_SCORE`), antwortet es mit dem festen
  Fallback-Text und ruft das Chat-Modell **nicht** auf.

---

## Troubleshooting

### `Ollama nicht erreichbar`

```bash
curl -s http://localhost:11434/api/tags
ollama serve   # falls nichts läuft
```

`OLLAMA_BASE_URL` in `.env` auf den richtigen Host setzen.

### `Qdrant nicht erreichbar`

```bash
curl -s http://localhost:6333/collections
```

`QDRANT_URL` in `.env` prüfen.

### `LibreOffice fehlt`

`/health` zeigt das nicht direkt an — du siehst es erst, wenn beim Ingest
ein `.doc`/`.xls` gemeldet wird:

> *Legacy .doc requires LibreOffice for conversion.*

Lösung: LibreOffice installieren (siehe oben) oder `.doc`/`.xls` vorab
manuell zu `.docx`/`.xlsx` konvertieren.

### `PDF hat keinen Text`

Status der Datei steht in der `documents`-Liste auf `requires_ocr`. Das
MVP enthält bewusst kein OCR — du kannst die PDF vorher mit `ocrmypdf`
durchlaufen lassen und dann erneut ingesten.

### `Embedding context length exceeded` bei XLSX

Wenn ein einzelner Spreadsheet-Chunk länger ist als das Kontextfenster des
Embedding-Modells, antwortet Ollama mit:

> *Ollama error 500: the input length exceeds the context length*

Das Backend fängt das ab, markiert die Datei in der `documents`-Tabelle
mit `status='failed'` und der vollen Fehlermeldung in `error_message`,
und macht mit dem Rest des Batches weiter.

Lösung — zwei Stellschrauben in `.env`:

```env
# Hard cap auf die Zeichenzahl je Spreadsheet-Chunk (Default 6000 ≈ 1500 Token)
XLSX_MAX_CHARS_PER_CHUNK=4000

# Oder weniger Zeilen pro Chunk
XLSX_ROWS_PER_CHUNK=20
```

Alternativ ein Embedding-Modell mit größerem Kontext verwenden
(z. B. `bge-m3`, das in unseren Tests sehr breite Maßnahmenpläne
problemlos verarbeitete).

### `Embedding-Dimension passt nicht`

Wenn du `EMBEDDING_MODEL` änderst, schlägt der Start mit einer Meldung
wie folgt fehl:

> *Qdrant collection 'documents' was created with vector dimension 1024,
> but the embedding model 'nomic-embed-text' produces vectors of
> dimension 768.*

Lösung — Collection neu anlegen:

```bash
curl -X DELETE http://localhost:6333/collections/documents
```

Beim nächsten Ingest wird sie mit der neuen Dimension automatisch erzeugt.

### `Keine Treffer in Qdrant`

* Wurde überhaupt erfolgreich ingestiert? `GET /documents?tenant=…&project=…`
  prüfen, `chunks_count` > 0?
* Ist der `MIN_RETRIEVAL_SCORE` zu hoch? Probiere `0.2`.
* Stimmen `tenant` und `project` exakt mit dem Ingest überein?

---

## Tests

```bash
cd backend
pytest -v
```

Die Tests decken ab:

1. Pfadsicherheit: Allow-list, Traversal, Systempfade
2. Scan eines erlaubten Pfads (rekursiv und nicht-rekursiv)
3. XLSX- und DOCX-Extraktion
4. Qdrant-Suchfilter (Tenant/Project-Pflicht)
5. Chat antwortet ohne Retrieval-Treffer mit dem Fallback-Text

Manuelle End-to-End-Tests siehe [`test_files/README.md`](./test_files/README.md).

---

## Projektstruktur

```
rag-qdrant-local/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app + routes
│   │   ├── config.py              # Pydantic settings
│   │   ├── database.py            # SQLite engine + session
│   │   ├── models.py              # SQLAlchemy ORM models
│   │   ├── schemas.py             # Pydantic request/response schemas
│   │   ├── path_security.py       # Allow-list + traversal guard
│   │   ├── source_scanner.py      # Walk + classify files
│   │   ├── document_loader.py     # PDF/DOCX/XLSX text extraction
│   │   ├── office_converter.py    # LibreOffice headless wrapper
│   │   ├── chunker.py             # Text + spreadsheet chunking
│   │   ├── ollama_client.py       # Embeddings + chat
│   │   ├── qdrant_store.py        # Vector store with mandatory filters
│   │   ├── ingestion_service.py   # Scan→hash→load→chunk→embed→upsert
│   │   ├── retrieval_service.py   # Embed query → Qdrant search
│   │   ├── chat_service.py        # Prompt assembly + persistence
│   │   ├── openai_compat.py       # /v1/chat/completions adapter
│   │   └── utils.py               # Hashing, ids, logging
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── storage/
│   ├── converted/                 # LibreOffice output
│   ├── temp/
│   └── rag.sqlite                 # Auto-created on first run
├── test_files/                    # Manual smoke fixtures (gitignored)
├── .env.example
└── README.md
```

---

## Optional: Docker-Compose für lokale Dev

Qdrant und Ollama werden hier **nicht** containerisiert — sie laufen
bereits am Host. Wenn du das Backend selbst containerisieren willst:

```bash
cd backend
docker build -t rag-qdrant-local .
docker run --rm -p 8000:8000 \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e QDRANT_URL=http://host.docker.internal:6333 \
  -e ALLOWED_BASE_PATHS=/data \
  -v /mnt/rag-data:/data:ro \
  rag-qdrant-local
```
