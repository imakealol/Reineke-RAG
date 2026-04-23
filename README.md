# Reineke-RAG

**A fully offline, enterprise-grade Retrieval-Augmented Generation system for internal Word, PDF and Excel documents — tuned for German/English corpora, running entirely on local hardware (Apple Silicon / x86 GPU).**

---

## What this is

A self-hosted "ChatGPT for your internal files" that:

- Ingests **PDF, DOCX and XLSX** reliably (including scanned PDFs, complex tables, formulas and multi-column layouts).
- Answers questions in German **and** English with **verifiable citations** (doc name, page, section).
- Handles four query styles: Q&A with citations, extraction, spreadsheet/table reasoning (SQL), and cross-document synthesis.
- Enforces **per-folder access control** driven by a central identity provider (SSO/OIDC).
- Runs **100 % offline** — no data ever leaves your machine/LAN. All models served by [Ollama](https://ollama.com/) and sibling containers.
- Scales from 500 to 10 000+ documents with an automated, watch-based ingestion pipeline.

## Why not just use the n8n self-hosted AI starter kit?

The n8n starter kit is a great *demo*, but it falls over on real-world office documents because it:

1. Uses a generic, fixed-size chunker that **destroys table structure** in PDFs and XLSX.
2. Has **no reranking step** — retrieval quality collapses once you exceed a few hundred docs.
3. Performs **dense-only retrieval**, missing exact-keyword matches (product codes, part numbers, names).
4. Treats every file as plain text — **spreadsheets lose their numeric semantics**, and multi-page DOCX sections get mashed together.
5. Offers **no citations, no ACLs, no audit log, no multi-user story**.

Reineke-RAG is opinionated about fixing each of these. See [docs/01_CONCEPT.md](docs/01_CONCEPT.md) for the full rationale.

## Who this is for

Built for and shaped by the requirements of a **DACH-region company** with:

- Mixed German/English internal documents (~500 – 10 000 files, growing).
- Company-wide usage with folder-based role access.
- Apple M-series workstation (tested on M4 Max / 64 GB) or Linux server with ≥ 1 GPU.
- Need for **reliable, cite-able, offline** answers on sensitive material.

## Document map

| # | Document | Purpose | Audience |
|---|----------|---------|----------|
| — | [README.md](README.md) | This file — orientation | Everyone |
| 01 | [docs/01_CONCEPT.md](docs/01_CONCEPT.md) | North-star concept, requirements, tech decisions, rationale | Decision makers, architect |
| 02 | [docs/02_ARCHITECTURE.md](docs/02_ARCHITECTURE.md) | Components, data flows, APIs, schemas, ports, volumes | Implementers, integrators |
| 03 | [docs/03_HANDBOOK.md](docs/03_HANDBOOK.md) | How to use the finished system day-to-day | End users |
| 04 | [docs/04_OPERATIONS.md](docs/04_OPERATIONS.md) | Install, upgrade, backup, monitoring, troubleshooting | Admin / IT |
| 05 | [docs/05_IMPLEMENTATION_PLAN.md](docs/05_IMPLEMENTATION_PLAN.md) | Phase-by-phase build plan with acceptance criteria | Build team / coordinator agent |
| 06 | [docs/06_AGENT_BRIEFS.md](docs/06_AGENT_BRIEFS.md) | Spec sheets for the coordinator + every subagent | Every AI agent building the stack |
| ADR | [docs/adr/](docs/adr/) | Architecture Decision Records — one per major choice, with alternatives considered | Architect, reviewers |

## Recommended reading order

- **Just skimming?** → README (this) → `01_CONCEPT.md` § "Architecture at a glance".
- **Going to build it?** → `01_CONCEPT.md` in full → `02_ARCHITECTURE.md` → `05_IMPLEMENTATION_PLAN.md` → `06_AGENT_BRIEFS.md`.
- **Going to run it?** → `04_OPERATIONS.md`.
- **Going to use it?** → `03_HANDBOOK.md`.

## Project status

This repository currently contains **concept + documentation only**. The actual stack is built by a coordinator agent that delegates to specialist subagents as described in `06_AGENT_BRIEFS.md`.

After the build, this file will be updated with a quick-start section and a `make up` entry point.

## License & acknowledgements

Planned stack components are all permissively licensed (Apache 2.0 / MIT). Inspiration taken from the n8n self-hosted AI starter kit and from IBM Docling's document-understanding research. See ADRs for component-specific attribution.
