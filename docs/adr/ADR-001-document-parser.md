# ADR-001 — Docling for document parsing

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

The single largest failure mode in "AI over my documents" systems is the **parse step**. A misread PDF produces garbage chunks; garbage chunks produce garbage retrieval; garbage retrieval produces hallucinated answers. Our corpus contains PDF (incl. scanned), DOCX, XLSX with a heavy German and English mix, often with:

- Multi-column layouts (technical specs).
- Tables inside PDFs (quotations, parts lists).
- Formulas and footnotes.
- Merged cells and multi-sheet XLSX.
- Scanned-PDF pages mixed with text-PDF pages in the same file.

We need one parser that handles all of these and emits a **structured document tree** (not flat text).

## Options considered

| Option | Pros | Cons |
|--------|------|------|
| **Docling** (IBM, Apache 2.0) | Best-in-class layout analysis; native table structure output; DOCX/XLSX/PDF/PPTX; OCR via EasyOCR or Tesseract; active development; built-in HybridChunker that respects structure. | Larger Docker image (~3 GB with OCR models); Python-only. |
| **Unstructured.io OSS** | Broad format coverage; good community. | Tables get flattened into text in the free edition; commercial pressure on OSS features; occasional quality regressions. |
| **LlamaParse (cloud)** | State-of-the-art quality. | **Cloud-only** → violates offline requirement. Rejected on hard rule. |
| **PyMuPDF + python-docx + openpyxl manually** | Fast and simple. | No layout awareness for PDFs, no table-structure recovery, a maintenance burden to glue together. |
| **Tika + Apache PDFBox** | JVM mature, battle tested. | Flat-text output; poor on tables; dated. |

## Decision

We use **Docling** as the *sole* parser. It is invoked behind a thin FastAPI wrapper (`services/docling`) and produces a `DoclingDocument` JSON plus, for XLSX (and confident PDF tables), a flat list of tables that downstream workers register in DuckDB.

Chunking uses Docling's **HybridChunker** (structure + max-tokens aware). We do not add a second chunker on top; if HybridChunker is insufficient for a specific doc type, we contribute upstream.

OCR defaults to **EasyOCR** (multilingual, GPU-optional, tidy API). Tesseract+`deu.traineddata` is available as a fallback via env flag for users who prefer it.

## Consequences

Positive:

- Tables survive ingestion → retrieval can match rows and columns.
- Section paths survive ingestion → every chunk answers "from which part of which document did this come?".
- One parser, one upgrade story.

Negative:

- A ~3 GB container image to distribute (manageable; pre-pulled in the offline bundle).
- If Docling ships a change that regresses on our corpus, we bear the upgrade pain. Mitigation: our eval set (Phase 8) is run automatically on parser upgrades.

## Fallbacks and escape hatches

- A feature flag `PARSER_FALLBACK=pymupdf` lets a specific document be re-ingested through a lightweight path if Docling crashes. Documented in operations.
- If Docling truly fails on a format pattern we care about, we vendor a small post-processor; we do **not** switch parser engines per doc type.
