# MediaWiki Connector

Imports MediaWiki content into Reineke-RAG so users can ask questions
against their wiki through the existing `/chat` and
`/v1/chat/completions` endpoints, with click-through citation URLs that
point back to the original wiki pages.

The connector keeps two modes in mind from the start:

| Mode | Customer provides | Status |
|---|---|---|
| **A — XML export (preferred)** | `wiki-current.xml` + `images/` upload dir + LocalSettings excerpt | **shipped** |
| **B — SQL mode** | MariaDB/MySQL dump + uploads + LocalSettings excerpt + wiki version | design only; shares everything from `service.py` downward |

The internal data model and the ingestion plumbing are identical for
both modes — only the input adapter differs.

---

## 1. Overview

The connector creates two kinds of `Document` rows:

* `source_type="mediawiki_page"` — one row per wiki page (latest
  revision), `source_path` is the canonical page URL.
* `source_type="mediawiki_upload"` — one row per referenced file
  (PDF/DOCX/XLSX/HTML/HTM/…), `source_path` is the resolved disk path.
  Reuses the existing file parsers.

Per-collection metadata lives in `Document.source_metadata_json`:

```json
{
  "page_id": 42, "revision_id": 117,
  "namespace_id": 0, "namespace_name": "",
  "categories": ["IT-Sicherheit", "Konzepte"],
  "linked_files": [
    {"title": "Datei:netzplan-demo.pdf", "bare_filename": "netzplan-demo.pdf"}
  ],
  "wiki_base_url": "https://wiki.demo.local",
  "article_path": "/wiki/$1", "script_path": "/w",
  "original_export_file": "/abs/path/wiki-current.xml",
  "page_url": "https://wiki.demo.local/wiki/Firewall_Konzept"
}
```

The Qdrant per-chunk payload carries `source_type`, `url`, `page_id`,
`revision_id`, and `namespace_id` so retrieval can render citations
without a SQLite lookup.

---

## 2. Recommended customer export (preferred path)

Ask the customer for these three artifacts and nothing else:

1. **`wiki-current.xml`** — MediaWiki XML export of the *current
   revisions* only (no history needed for retrieval).
2. **`images/`** (or `uploads/`) — the raw upload directory.
   Either hashed (`images/a/ab/Filename.pdf`, MediaWiki default) or
   flat (`images/Filename.pdf`) layout works.
3. **`LocalSettings.example.php`** — the parts that reveal URL layout
   *only*. The connector reads `$wgServer`, `$wgArticlePath`, and
   `$wgScriptPath` via regex; it never executes PHP. Strip every
   credential, every secret key, every API token before sharing.

Optional: `checksums.sha256` for integrity verification.

---

## 3. Customer export instructions

### XML export (preferred)

Run the official MediaWiki maintenance script on the customer's wiki:

```bash
# MediaWiki >= 1.40
php maintenance/run.php dumpBackup \
    --current \
    --quiet \
    --output=file:/tmp/wiki-current.xml

# Older releases
php maintenance/dumpBackup.php --current --quiet > /tmp/wiki-current.xml
```

Then copy the upload directory:

```bash
tar -C /path/to/mediawiki -cf /tmp/wiki-images.tar images/
```

### LocalSettings excerpt

Send only:

```php
$wgServer       = "https://wiki.example.com";
$wgScriptPath   = "/w";
$wgArticlePath  = "/wiki/$1";
```

Strip everything else - passwords, API keys, OAuth secrets, mail
settings. The connector does not read them.

---

## 4. What NOT to send to coding agents or cloud tools

The connector runs fully locally, but customer data may pass through
your hands during setup. Never send to chat-based assistants, cloud IDEs,
or shared storage:

* Real MySQL/MariaDB SQL dumps.
* Real `images/` uploads.
* `LocalSettings.php` with passwords or `$wgSecretKey`.
* Screenshots of confidential pages.
* `.env` files with `MYSQL_PASSWORD`, `WIKI_BOT_TOKEN`, etc.

The dummy dataset in `backend/tests/connectors/mediawiki/fixtures/` is
the only export anyone should drop into a public repo.

---

## 5. CLI usage

`inspect-xml` is a read-only dry-pass that writes nothing — safe to run
on a real customer export to scout namespace counts and file references
before importing.

```bash
python -m app.connectors.mediawiki.cli inspect-xml \
    --xml-path /mnt/rag-data/demo/wiki/export/wiki-current.xml \
    --uploads-path /mnt/rag-data/demo/wiki/export/images \
    --allowed-namespaces 0
```

`import-xml` runs the full pipeline (use `--dry-run` to count without
writing):

```bash
python -m app.connectors.mediawiki.cli import-xml \
    --tenant demo \
    --project wiki \
    --xml-path /mnt/rag-data/demo/wiki/export/wiki-current.xml \
    --uploads-path /mnt/rag-data/demo/wiki/export/images \
    --wiki-base-url https://wiki.demo.local \
    --article-path "/wiki/\$1" \
    --allowed-namespaces 0 \
    --include-uploads \
    --reindex-changed-only
```

Both `--xml-path` and `--uploads-path` must resolve under one of the
`ALLOWED_BASE_PATHS` configured in `.env`. Anything else is rejected
before the parser sees a byte.

---

## 6. API usage

```http
POST /sources/mediawiki/import-xml
Content-Type: application/json

{
  "tenant": "demo",
  "project": "wiki",
  "xml_path": "/mnt/rag-data/demo/wiki/export/wiki-current.xml",
  "uploads_path": "/mnt/rag-data/demo/wiki/export/images",
  "wiki": {
    "base_url": "https://wiki.demo.local",
    "article_path": "/wiki/$1",
    "script_path": "/w"
  },
  "allowed_namespaces": [0],
  "include_redirects": false,
  "include_uploads": true,
  "reindex_changed_only": true,
  "dry_run": false
}
```

Response:

```json
{
  "status": "ok",
  "mode": "xml",
  "dry_run": false,
  "pages_seen": 20,
  "pages_indexed": 6,
  "pages_skipped_namespace": 8,
  "pages_skipped_redirect": 1,
  "pages_skipped_unchanged": 5,
  "files_seen": 4,
  "files_indexed": 3,
  "files_skipped_unsupported": 1,
  "files_skipped_unchanged": 0,
  "unresolved_files": [],
  "warnings": [],
  "errors": []
}
```

`status` is `"partial"` if `errors` is non-empty.

---

## 7. Dummy dataset usage

The repository ships a 5-page synthetic export at
`backend/tests/connectors/mediawiki/fixtures/`:

* `wiki-current.xml` — 1 main-NS page with categories & file refs,
  1 main-NS page with 2 revisions (only the latest is indexed),
  1 redirect, 1 Talk page, 1 Template page.
* `images/Netzplan-demo.pdf` — placeholder upload (flat layout).
* `LocalSettings.example.php` — server / article_path / script_path
  only, no secrets.

Use it for local end-to-end smoke tests:

```bash
python -m app.connectors.mediawiki.cli inspect-xml \
    --xml-path "$(pwd)/tests/connectors/mediawiki/fixtures/wiki-current.xml" \
    --uploads-path "$(pwd)/tests/connectors/mediawiki/fixtures/images" \
    --allowed-namespaces 0
```

`ALLOWED_BASE_PATHS` in `.env` must include the fixtures directory for
the path validation to succeed.

---

## 8. SQL mode notes (Mode B - design only)

For customers who can only provide a MariaDB/MySQL dump:

1. Restore the dump into a local MariaDB/MySQL instance — never
   point the connector at the customer's production database.
2. Grant a read-only user (`GRANT SELECT ON wiki.*`); set the session
   `SET TRANSACTION READ ONLY` defensively.
3. Extract via the same shape as `MediaWikiPage`. The connector reads
   only:
   * `page`, `revision`, `slots`, `content` (modern schema), or
     `page`, `revision`, `text` (legacy schema)
   * `categorylinks`, `image`, `imagelinks` if available
4. Skip every table that holds user data, secrets, or transient state:
   `user`, `user_properties`, `user_groups`, `bot_passwords`,
   `watchlist`, `sessions`, `tokens`, `objectcache`, `logging`,
   `recentchanges`, `archive`, `filearchive`, `change_tag`.
5. Use `page_latest` to pick the current revision; never index old
   revisions in MVP.

The SQL extractor will produce the same `MediaWikiPage` dataclass and
feed the same service method. Everything from chunking onward is
shared with Mode A.

This mode is not implemented yet. Planned endpoint:
`POST /sources/mediawiki/import-sql`.

---

## 9. Security notes

* `xml_path` and `uploads_path` are validated against
  `ALLOWED_BASE_PATHS` via the same `resolve_safe_path` choke point
  used by `/sources/ingest-path`.
* `[[File:...]]` filenames containing `..`, `/`, `\`, or NUL are
  rejected before any path arithmetic. The resolved upload path is
  then verified to live within `uploads_path` (defence against
  symlink escapes).
* Templates `{{...}}` are stripped, never evaluated. The connector
  doesn't have the template source and doesn't fetch it.
* External URLs in `[...]` are kept as plain text; the connector never
  follows them.
* DB credentials are never logged. Mode B will accept a read-only
  password via the request body; that body is treated as short-lived.
* Dry-run output reports counts, namespace breakdowns, and lists of
  unresolved filenames — never full page bodies.
* No telemetry. No external API calls during normal operation.

---

## 10. Limitations (MVP)

The hand-rolled normalizer handles the markup we have observed in real
DACH ISMS/IT-security wikis. It deliberately does NOT:

* execute MediaWiki templates or Lua modules,
* expand parser functions (`{{#if}}`, `{{#switch}}`, ...),
* render math (`<math>...</math>`),
* OCR image uploads,
* index old revisions,
* import Talk / User / Template namespaces by default
  (configurable via `allowed_namespaces`),
* parse complex tables with row/column-spans (falls back to a readable
  cell-per-line dump in that case).

If a real customer wiki exposes markup the normalizer butchers,
the next step is to swap in `mwparserfromhell` behind the same
`normalize_wikitext` entry point - same return type, no caller change.
