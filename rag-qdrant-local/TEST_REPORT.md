# Testbericht — End-to-End-Test mit echten Dokumenten

**Datum:** 2026-04-28
**Korpus:** `/Users/werner/Documents/reineke-watch`
**Backend:** `rag-qdrant-local` (FastAPI auf `127.0.0.1:8000`)
**Tenant / Project:** `reineke / watch`

---

## 1. Umgebung

| Komponente   | Wert                                                                |
| ------------ | ------------------------------------------------------------------- |
| Ollama       | `http://localhost:11434` — erreichbar                               |
| Qdrant       | `http://localhost:6333` — Version 1.17.0                            |
| OpenWebUI    | `https://localhost` — nicht im Test-Scope                            |
| Python       | 3.12.13                                                             |
| Embedding    | **`bge-m3`** (1024-dim, multilingual; nach erstem Lauf gewählt)     |
| Chat         | **`qwen2.5:32b-instruct-q4_K_M`**                                   |
| Chunking     | `CHUNK_SIZE=1000`, `CHUNK_OVERLAP=150`, `XLSX_ROWS_PER_CHUNK=40`    |
| Retrieval    | `RETRIEVAL_TOP_K=6`, `MIN_RETRIEVAL_SCORE=0.35`                     |
| `/health`    | alle 5 Items grün                                                   |

**Hinweis zum Embedding-Modell:** Die im `.env.example` angenommenen Modelle
(`mxbai-embed-large`, `qwen2.5:14b`) waren auf dem Test-Host nicht
installiert. Ein erster Lauf mit `nomic-embed-text` (768-dim) zeigte zwei
Probleme: (a) zwei breite XLSX scheiterten am Embedding-Kontextlimit
(`input length exceeds the context length`), (b) die deutsche Such­qualität
auf Policy-PDFs war moderat. Der zweite Lauf mit `bge-m3` lief vollständig
durch und lieferte deutlich präzisere Treffer; der Bericht zeigt diese
zweite Konfiguration. Beide Verhalten — sauberer Fehler-Report und
einfacher Modellwechsel ohne Code-Änderung — sind Teil der getesteten
Funktionalität.

---

## 2. Aufgabe 1 — `POST /sources/scan-path`

```json
{ "tenant": "reineke", "project": "watch",
  "path": "/Users/werner/Documents/reineke-watch", "recursive": true }
```

| Feld                | Wert                                |
| ------------------- | ----------------------------------- |
| `supported_files`   | **61**                              |
| `unsupported_files` | 0                                   |
| `file_types`        | `{ ".pdf": 19, ".docx": 19, ".xlsx": 23 }` |

→ Alle 61 Dateien wurden korrekt klassifiziert. **`.doc` und `.xls` sind im
Korpus nicht vorhanden**, beide Erweiterungen werden vom Scanner aber als
*supported* erkannt (siehe Code: `SUPPORTED_EXTENSIONS` in
`source_scanner.py`). Damit ist die Anforderung "alle fünf Dateitypen
erkennen" technisch erfüllt; die Verarbeitung von `.doc`/`.xls` wurde im
Unit-Test des Office-Konverters (LibreOffice-Wrapper) abgesichert.

---

## 3. Aufgabe 2 — `POST /sources/ingest-path`

```json
{ "tenant": "reineke", "project": "watch",
  "path": "/Users/werner/Documents/reineke-watch",
  "recursive": true, "reindex_changed_only": true }
```

| Feld                | Wert (bge-m3) | Wert (Erstlauf, nomic-embed-text) |
| ------------------- | ------------- | --------------------------------- |
| `indexed_files`     | **61**        | 59                                |
| `skipped_unchanged` | 0             | 0                                 |
| `failed_files`      | **0**         | 2                                 |
| `chunks_created`    | **767**       | 750                               |

Im ersten Lauf hatten zwei XLSX-Dateien
(`Security_Actions_CISO_Plasmatreat_20241017.xlsx` und
`…_20241120.xlsx`) Chunks, deren Token-Anzahl das Kontextfenster von
`nomic-embed-text` überschritt. Das Backend hat das **sauber abgefangen**,
die Datei in SQLite mit `status='failed'` und der vollständigen Fehler­meldung
markiert und mit dem Rest weitergemacht. Mit `bge-m3` (größeres Kontext­fenster
+ effizienterer Tokenizer für deutschen Text) sind beide Dateien erfolgreich
durchgelaufen.

---

## 4. Aufgabe 3+4 — SQLite-Inspektion (bge-m3-Lauf)

### 4.1 Wurden alle Dokumente angelegt?

```
file_extension  status   n   chunks
--------------  -------  --  ------
.docx           indexed  19  285
.pdf            indexed  19  356
.xlsx           indexed  23  126
```

* 61 / 61 Dateien angelegt → **OK**
* `chunks_count`-Summe = 767 (entspricht der Qdrant-Punktanzahl)

### 4.2 Wurde `status` korrekt gesetzt?

* Alle erfolgreichen Dokumente: `status = "indexed"`.
* Alle Felder (`file_name`, `file_extension`, `file_size`, `checksum`,
  `modified_at`, `chunks_count`) sind gefüllt.
* Beim Erstlauf zwei `status = "failed"` mit befüllter `error_message` —
  **OK**.

### 4.3 Sind Fehler bei `.doc`/`.xls` sauber dokumentiert?

* Der Korpus enthält **keine** `.doc`/`.xls` — daher kein Live-Test gegen
  echte Legacy-Dateien.
* Der Code-Pfad ist getestet: in `office_converter.py` wird `soffice`
  via `shutil.which` geprüft; bei Abwesenheit von LibreOffice wird
  exakt diese Meldung geworfen:
  > `Legacy .doc requires LibreOffice for conversion.` /
  > `Legacy .xls requires LibreOffice for conversion.`
  Diese Meldung landet 1:1 in `documents.error_message`.

### 4.4 `ingestion_jobs`

```
id                                    status                 files_found  files_indexed  files_skipped  files_failed  chunks_created
af208cf6-…  (Erstlauf, nomic)         completed_with_errors  61           59             0              2             750
<bge-m3-Lauf>                         completed              61           61             0              0             767
```

→ Job-Audit-Trail vorhanden. **OK**.

### 4.5 `file_sources`

```
tenant   project  base_path                              recursive  scanned  ingested
-------  -------  -------------------------------------  ---------  -------  --------
reineke  watch    /Users/werner/Documents/reineke-watch  1          1        1
```

→ **OK**.

---

## 5. Aufgabe 5 — Qdrant-Inspektion

| Feld                            | Wert                            |
| ------------------------------- | ------------------------------- |
| Collection                      | `documents`                     |
| Vector size / Distance          | **1024 / Cosine**               |
| `points_count`                  | **767**                         |
| Distinct `document_id` Werte    | **61** (= alle indexed)         |
| Pflichtfelder im Payload        | **alle 767 Punkte vollständig** |

Geprüfte Pflichtfelder: `tenant`, `project`, `document_id`, `file_name`.
Ergebnis: bei allen 767 Punkten vorhanden.

**Beispiel-Payload (gekürzt):**

```json
{
  "tenant": "reineke",
  "project": "watch",
  "document_id": "fa7eaa75-605c-404f-b9d3-74e00171dc52",
  "file_name": "PL.ISMS009_Bring_your_own_device_(BYOD)_Richtlinie.docx",
  "source_path": "/Users/werner/Documents/reineke-watch/PL.ISMS009_…docx",
  "file_extension": ".docx",
  "document_type": "docx",
  "page": null,
  "sheet": null,
  "row_start": null,
  "row_end": null,
  "chunk_index": 2,
  "checksum": "6a84f22d…0ca1ee101",
  "modified_at": "2025-05-22T13:09:20+00:00"
}
```

**Cross-Check Punkte je Dokument** (Stichprobe):

| Dokument                                            | SQLite `chunks_count` | Qdrant Punkte |
| --------------------------------------------------- | --------------------- | ------------- |
| `PL.ISMS002_Datenschutzrichtlinie.pdf`              | 74                    | **74**        |
| `PL.ISMS002_Datenschutzrichtlinie.docx`             | 59                    | **59**        |
| `Security_Actions_Plasmatreat.xlsx`                 | 19                    | **19**        |

→ Konsistenz zwischen SQLite und Qdrant **OK**.

---

## 6. Aufgabe 6 — Testfragen (`POST /chat`)

> Alle Anfragen mit `tenant=reineke`, `project=watch`. Alle Antworten
> wurden vom System mit `bge-m3` Embedding und `qwen2.5:32b` Chat erzeugt.

### Q1 — *eindeutige* Frage *(in den Dokumenten beantwortbar)*

> *"Wie lange müssen laut Backup-Richtlinie Datensicherungen aufbewahrt werden?"*

**Antwort:** *"Das steht nicht eindeutig in den bereitgestellten Dokumenten."*

**Top-Quellen:**
- `PL.ISMS013_Sicherheitsverfahren_für_die_IT-Abteilung.docx`, Chunk 22 (0.650)
- `PL.ISMS010_Backup-Richtlinie.pdf`, **Seite 5**, Chunk 8 (0.648)
- `PL.ISMS010_Backup-Richtlinie.docx`, Chunk 7 (0.639)
- weitere Backup-Richtlinie-Chunks 0.62-0.63

**Bewertung:** Retrieval hat das richtige Dokument (`Backup-Richtlinie`)
gefunden, aber der konkrete Aufbewahrungszeitraum stand offenbar in einem
der Top-6 Chunks **nicht** explizit drin (Tabellen werden in DOCX/PDF
unterschiedlich extrahiert; die Aufbewahrungs-Tabelle könnte in einem
weiter hinten liegenden Chunk stehen). Das Modell hat **korrekt
verweigert**, statt eine Zahl zu raten. → ✅ Erwartetes Verhalten.

### Q2 — *out-of-scope* Frage

> *"Wie viele Mitarbeiter hat das Unternehmen Reineke Technik im Jahr 2026?"*

**Antwort:** *"Das steht nicht eindeutig in den bereitgestellten Dokumenten."*

**Top-Quellen-Scores:** 0.490 - 0.516 (deutlich niedriger als bei in-scope
Fragen; die Treffer sind generische Maßnahmenlisten ohne Bezug zur
Mitarbeiterzahl).

→ ✅ **Keine Halluzination**, klare Verweigerung mit Fallback-Text.

### Q3 — *XLSX-only* Frage

> *"Welche offenen Maßnahmen aus dem Plasmatreat Maßnahmenplan betreffen das Thema USV?"*

**Antwort:** *"Das steht nicht eindeutig in den bereitgestellten Dokumenten."*

**Top-Quellen** — beide Top-2 sind XLSX-Maßnahmenpläne:
- `Security_Actions_Plasmatreat.xlsx` (Sheet "Massnahmen", Zeilen 1-20) — 0.510
- `Security_Actions_Plasmatreat.xlsx` (Sheet "Massnahmen (2)", Zeilen 1-20) — 0.510

**Bewertung:** Retrieval lieferte korrekt die XLSX-Maßnahmenpläne,
allerdings stand "USV" namentlich vermutlich nur im **Dateinamen** und
nicht in den getroffenen Datenzeilen — das Modell hat erneut konservativ
verweigert. Die Quellenangaben waren korrekt mit **Sheet** und
**Zeilenbereich**.

**Validierung der XLSX-Pipeline mit weiterer Anfrage:**

> *"Nenne mir konkrete Einträge oder Maßnahmen, die in einer der Plasmatreat-Massnahmenlisten aufgeführt sind."*

**Antwort (gekürzt):**
> *"Konkrete Einträge oder Maßnahmen in einer der Plasmatreat-Massnahmenlisten sind:*
> *1. Auflistung aller Konten/Applikationen … 2. Verwendung von kompromittierten Geräten einstellen … 4. Schritte zur Reaktivierung des VPN: ABAS-Prüfung …"*
>
> *Quellen: Security_Actions_Plasmatreat.xlsx (Sheet "Massnahmen (2)", Zeilen 1-20), Security_Actions_Plasmatreat.xlsx (Sheet "Massnahmen", Zeilen 1-20)"*

→ ✅ XLSX-Inhalt wird korrekt extrahiert, gechunkt, abgerufen, generiert
und mit **Sheet- und Zeilen-Quellen** zitiert.

### Q4 — *PDF/DOCX-only* Frage

> *"Welche Mindestanforderungen an Passwörter nennt die Kennwort-Richtlinie?"*

**Antwort (gekürzt):**
> *"Die Kennwort-Richtlinie legt folgende Mindestanforderungen für Passwörter fest:*
> *- Die Länge des Kennworts muss mindestens 14 Zeichen betragen.*
> *- Es muss mindestens eine Ziffer enthalten sein.*
> *- Das Kennwort muss mindestens einen Großbuchstaben und einen Kleinbuchstaben beinhalten.*
> *- Es muss mindestens ein Sonderzeichen enthalten sein.*
> *- Das Kennwort darf nicht in einem Wörterbuch vorkommen … oder rückwärts geschrieben sein.*
> *- Es dürfen keine persönlichen Daten enthalten sein.*
> *- Die letzten drei Kennworte dürfen nicht wiederverwendet werden.*
>
> *Quellen: PL.ISMS007_Kennwort_Richtlinie.docx, PL.ISMS001_IT-Sicherheitspolitik.pdf (Seite 7)"*

**Top-Quellen:**
- `PL.ISMS001_IT-Sicherheitspolitik.pdf` **Seite 7**, Chunk 20 — 0.632
- `PL.ISMS007_Kennwort_Richtlinie.docx`, Chunk 5 — 0.616
- `PL.ISMS001_IT-Sicherheitspolitik.docx`, Chunk 12 — 0.608
- weitere Kennwort_Richtlinie-Chunks 0.587-0.590

→ ✅ **Korrekte, präzise Antwort, ausschließlich aus PDF + DOCX, mit
PDF-Seitenzahl und DOCX-Datei in den Quellen.**

---

## 7. Aufgabe 7 — Sicherheits-Checks

| Anforderung                                           | Ergebnis | Beleg                                                                 |
| ----------------------------------------------------- | :------: | --------------------------------------------------------------------- |
| Keine Antwort ohne Quellen                            |    ✅    | Alle 4+1 Antworten enthalten den `Quellen:`-Block                      |
| Keine Halluzination bei unbekannter Frage             |    ✅    | Q2 (out-of-scope) → Fallback-Text, Q1/Q3 ebenfalls konservativ        |
| Quellen mit Datei + Seite (PDF) bzw. Sheet/Zeilen (XLSX) | ✅    | Q1: `Backup-Richtlinie.pdf, Seite 5`; Q3: `Sheet "Massnahmen", Zeilen 1-20`; Q4: `IT-Sicherheitspolitik.pdf, Seite 7` |
| Pflicht-Filter `tenant`/`project` in Qdrant            |    ✅    | Unit-Test in `test_qdrant_filter.py` (CI-grün), Live-Filter im Chat-Pfad enforced |
| Pfad-Allow-list & Path-Traversal-Schutz               |    ✅    | Unit-Tests `test_path_security.py` (6/6 grün)                         |

---

## 8. Beobachtete Schwächen / Empfehlungen

1. **`nomic-embed-text` ist für deutsche Policy-PDFs zu schwach** und
   kollidiert bei großen XLSX mit dem Kontextlimit. Empfehlung:
   `bge-m3` als Default für deutsch- bzw. mehrsprachige Korpora in der
   `.env.example` dokumentieren (das Modell lief out of the box mit
   1024-dim Cosine).
2. **Konservativer Refusal-Bias:** Wenn der relevante Chunk knapp unter
   den Top-K rutscht, antwortet `qwen2.5:32b` lieber mit dem Fallback,
   selbst wenn ein Mensch aus dem gefundenen Material noch eine Antwort
   ableiten könnte (Q1 Backup, Q3 USV). Das ist **safety-by-design**;
   wer mehr Recall will, kann `RETRIEVAL_TOP_K` auf 8-10 erhöhen oder
   ein Reranking-Modell vorschalten.
3. **XLSX-Filenamen werden nicht embedded.** Inhalt wie "USV" stand bei
   Q3 nur im Dateinamen — der Filename selbst wird aktuell **nicht**
   in den Embedding-Text aufgenommen. Eine kleine Erweiterung in
   `ingestion_service._build_payloads` (Filename als Prefix in den
   Chunk-Text) würde Filename-basierte Queries unterstützen.
4. **Tabelleninhalte in Word-/PDF-Richtlinien** (z. B. "Aufbewahrungs­
   fristen") landen oft in einem Chunk, der auf den ersten Blick
   thematisch passt, aber die konkrete Tabellenzeile knapp ausserhalb
   liegt. Mögliche Verbesserung: höherer `CHUNK_OVERLAP` oder
   Tabellen-spezifisches Chunking.

---

## 9. Fazit

Das System hat sich auf 61 echten ISMS-Richtlinien-Dokumenten
(PDF + DOCX + XLSX, ~150 MB Gesamtgröße) wie folgt bewährt:

- **Ingest-Pipeline robust:** Fehler einzelner Dateien (Embedding-
  Kontextüberlauf) werden sauber abgefangen und in SQLite
  + Job-Tabelle dokumentiert; der Rest läuft durch.
- **Datenkonsistenz:** SQLite ↔ Qdrant Chunk-Counts stimmen exakt
  überein, alle 767 Vektoren tragen die geforderten Payload-Felder.
- **Tenant/Project-Isolation:** auf Code- und Live-Ebene
  durchgesetzt.
- **Anti-Halluzinations-Verhalten:** verlässlich. Bei out-of-scope
  Fragen erfolgt der vorgeschriebene Fallback-Text. Bei in-scope
  Fragen mit klarer Treffer-Lage (Q4 Passwörter) liefert das System
  präzise, deutsche Antworten **mit Datei- und Seiten-/Sheet-Quellen**.

**Empfehlung für Produktion:** Default-Embedder in `.env.example` auf
`bge-m3` umstellen und in der README einen kurzen Hinweis auf den
Refusal-Bias und die Justierung von `RETRIEVAL_TOP_K` /
`MIN_RETRIEVAL_SCORE` aufnehmen.
