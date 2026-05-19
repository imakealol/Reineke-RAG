# Retrieval-Quality Eval

Ein versionierter Satz von Fragen mit erwarteten Antworten, gegen den wir
Retrieval-Qualität vor und nach jedem Feature messen. Liefert vier
Headline-Metriken und einen Diff gegen den letzten Lauf.

## Aufbau

```
tests/eval/
├── questions.yaml          ← die Fragen (per Hand gepflegt, versioniert)
├── conftest.py             ← Fixtures: Backend-URL, Tenant, Project
├── test_eval_retrieval.py  ← Runner + Scoring + Scorecard
└── results/                ← .gitignore — pro Lauf eine eval-*.json
```

## Voraussetzungen

- Backend läuft (`uvicorn app.main:app --host 0.0.0.0 --port 8000`)
- Qdrant + Ollama erreichbar und mit der zu prüfenden Tenant/Project-Kombi
  bereits ingestiert

## Pass / Fail-Semantik

Standard: **das ist ein Bericht, kein Gate.** Grün heißt nur, dass das
Eval-Harness durchgelaufen ist (Backend war erreichbar, alle Fragen
produzierten ein Ergebnis). **Qualität liest du aus der Scorecard.** Direkt
unter der Scorecard steht jetzt zusätzlich eine deutliche TL;DR-Zeile:

```
⚠ Outcome: 3/13 questions met expectation (23.1 %).
  Note: green PASSED below only means the eval harness completed;
  quality is the number above.
```

Wenn du in einem Quality-PR die Eval als **harten Gate** willst, setze
`RAG_EVAL_MIN_OUTCOME_RATE` auf einen Schwellwert zwischen 0 und 1:

```bash
RAG_EVAL_MIN_OUTCOME_RATE=0.7 pytest -m eval -v -s tests/eval/
```

Dann scheitert pytest mit einer klaren Assertion, sobald weniger als 70 %
der Fragen ihre Erwartung erfüllen.

## Ausführen

```bash
cd rag-qdrant-local/backend
source .venv/bin/activate

# Default: localhost:8000, Tenant=reineke, Project=watch
pytest -m eval -v -s tests/eval/test_eval_retrieval.py

# Andere Backend-Instanz / Tenant
RAG_EVAL_BACKEND_URL=http://10.1.1.81:8000 \
RAG_EVAL_TENANT=ruberg \
RAG_EVAL_PROJECT=versuchsprotokolle \
pytest -m eval -v -s tests/eval/test_eval_retrieval.py
```

`-s` ist wichtig — sonst schluckt pytest die Scorecard-Ausgabe.

### Schneller Modus: Retrieval-only

Beim iterativen Arbeiten an der **Retrieval-Schicht** (Chunking, Embeddings,
Hybrid-Suche, Reranker) braucht man die LLM-Antwort nicht — Recall@K und
MRR sind reine Retrieval-Metriken. Mit `RAG_EVAL_RETRIEVAL_ONLY=1` läuft
die Eval gegen `/retrieve` statt `/chat`:

```bash
RAG_EVAL_RETRIEVAL_ONLY=1 pytest -m eval -v -s tests/eval/
```

Effekt:
- ~30 s statt ~30 min pro Lauf (kein LLM-Call)
- Refusal-Fragen werden übersprungen (kein Antworttext zum Prüfen)
- Faithfulness wird nicht gemessen (gleiches Argument)
- Recall@K, MRR, By-category und Latenz bleiben aussagekräftig

Für PR-Belege weiter den vollen Lauf mit `/chat` machen — der schnelle
Modus ist nur für den Dev-Loop.

## Metriken

| Metrik | Bedeutung |
|---|---|
| **Recall@5** | erwartetes Dokument unter den Top-5 Quellen? |
| **MRR** | mittlerer reziproker Rang des ersten richtigen Dokuments |
| **Faithfulness** | Antwort enthält mindestens einen `expected_keywords`-Begriff |
| **Refusal-Accuracy** | für `expected_refusal: true`-Fragen: wurde wirklich abgelehnt? |
| **Latency p50/p95** | Query-Laufzeit (wichtig wenn neue Features Latenz kosten) |

## Fragen pflegen

Format pro Eintrag in `questions.yaml`:

```yaml
- id: q01                                       # stabile ID, wird im Diff genutzt
  category: named_entity                        # named_entity | category_query
                                                # | refusal | table_extraction
  difficulty: easy                              # easy | medium | hard (nur Anzeige)
  question: "Wurden bei Versuch X Y genutzt?"   # User-Frage
  expected_doc: "1576 Lubrizol"                 # case-insensitive Substring
                                                # auf file_name
  expected_keywords: ["Konusmischer"]           # ≥1 muss im Antworttext sein
```

Für unscharfe Fragen mit mehreren akzeptablen Quellen:

```yaml
  expected_docs_any: ["NaCl", "Natriumchlorid", "Kalisalz"]
```

Für Fragen die abgelehnt werden müssen:

```yaml
  expected_refusal: true
  # expected_keywords entfällt
```

## Output

Auf der Konsole:

```
========================================================================
 Reineke-RAG Retrieval Quality Eval
 Run:      2026-05-19T11:42:33Z
 Backend:  http://localhost:8000
 Tenant:   reineke / watch
 Questions: 12 (10 answer · 2 refusal)
========================================================================

Aggregate
─────────
  Recall@5         7/10 ( 70.0 %)
  MRR              0.450
  Faithfulness     8/10 ( 80.0 %)
  Refusal-Accuracy 2/2  (100.0 %)
  Latency p50/p95  2.10s / 4.80s

By category
───────────
  category_query    Recall@5  0/3  (  0.0 %)   MRR 0.00
  named_entity      Recall@5  6/6  (100.0 %)   MRR 0.83
  table_extraction  Recall@5  1/1  (100.0 %)   MRR 0.50

Misses
──────
  q07   [hard]    'Gibt es Versuche, bei denen Salze gemischt werden?' — expected source not in top-5
  q08   [hard]    'Welche Versuche verwenden pulverförmige Rohstoffe?' — expected source not in top-5
```

Zusätzlich landet ein vollständiger JSON-Scorecard unter
`results/eval-YYYYMMDDTHHMMSSZ.json` — beim nächsten Lauf wird der jüngste
JSON-File automatisch als Baseline herangezogen und der Diff angezeigt
(`+5.0%` etc. neben jeder Metrik).

## Workflow für Quality-PRs

1. Vor dem Feature: `pytest -m eval -s` → Baseline-Scorecard
2. Feature implementieren auf Branch
3. Backend neu starten mit Feature-Flag an
4. `pytest -m eval -s` → neue Scorecard mit Diff
5. PR-Beschreibung enthält den Vorher/Nachher-Auszug

CI führt die Eval **nicht** aus (`pytest -m "not eval"` im Workflow) — sie
braucht eine live Qdrant/Ollama-Umgebung und ist auf die Größe des
ingestierten Korpus zugeschnitten. Eval ist ein **manueller Gate** vor
Quality-PRs, kein automatischer.
