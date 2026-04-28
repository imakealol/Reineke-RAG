# test_files/

Drop test fixtures here for end-to-end manual testing. The repository does
not ship binary samples — create them locally before running the smoke
tests below.

Recommended set:

| File          | How to obtain                                                                                       |
| ------------- | --------------------------------------------------------------------------------------------------- |
| `sample.pdf`  | Any short text PDF; or `echo "hello world" \| enscript -p sample.ps && ps2pdf sample.ps sample.pdf` |
| `sample.docx` | Save a one-page Word document.                                                                      |
| `sample.doc`  | "Save As → Word 97-2003" from Word, or `soffice --convert-to doc sample.docx`.                      |
| `sample.xlsx` | Any small spreadsheet with a header row.                                                            |
| `sample.xls`  | "Save As → Excel 97-2003" from Excel, or `soffice --convert-to xls sample.xlsx`.                    |

## Manual smoke run

Once the backend is up (see top-level `README.md`):

```bash
# 1. Add this directory to ALLOWED_BASE_PATHS in your .env, e.g.:
#    ALLOWED_BASE_PATHS=/absolute/path/to/rag-qdrant-local/test_files

# 2. Scan
curl -s -X POST http://localhost:8000/sources/scan-path \
  -H "Content-Type: application/json" \
  -d '{
    "tenant": "demo",
    "project": "smoke",
    "path": "/absolute/path/to/rag-qdrant-local/test_files",
    "recursive": true
  }' | jq

# 3. Ingest
curl -s -X POST http://localhost:8000/sources/ingest-path \
  -H "Content-Type: application/json" \
  -d '{
    "tenant": "demo",
    "project": "smoke",
    "path": "/absolute/path/to/rag-qdrant-local/test_files",
    "recursive": true,
    "reindex_changed_only": true
  }' | jq

# 4. Ask
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant": "demo",
    "project": "smoke",
    "question": "Worum geht es in den Dokumenten?"
  }' | jq
```
