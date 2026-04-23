# 03 — User Handbook

> This handbook is for **end users** of Reineke-RAG: people who log in, ask questions, upload documents (if authorised), and expect correct answers with sources. It is bilingual where it matters; most sections are in English with German parallels for UI-visible strings.

---

## 1. What Reineke-RAG does — and does not do

**Does:**

- Answers questions about your company's internal documents (Word, PDF, Excel) in German or English.
- Shows **sources** for every claim: file name, page, and a clickable preview.
- Understands tables in Excel and in PDFs — can answer "which project had the highest margin in 2024?" numerically.
- Respects **folder permissions** — you only see and search documents you are allowed to read.

**Does not:**

- Does not browse the web. Everything stays inside your company.
- Does not write or send emails on your behalf.
- Does not modify your documents. It only reads them.
- Does not guess. If no accessible document contains the answer, it will say so.

If you have an expectation outside these lines, **ask your administrator** — it may be a v1.1 feature or a misconfiguration.

---

## 2. Logging in

1. Open `https://rag.<your-company>.local` (the address your admin shared).
2. You will be redirected to the **SSO login page** ("Authentik"). Use your company credentials.
3. On first login, you will be asked to change your password and (optionally) register a second factor.
4. After login you land in the **chat UI**.

If the browser warns about the certificate the first time, your admin can distribute the internal CA — follow their instructions. Do not bypass the warning on public Wi-Fi.

---

## 3. The chat screen — a tour

```
┌────────────────────────────────────────────────────────────┐
│  Reineke-RAG                         [DE | EN]   [User ▾] │
├───────────────────────┬────────────────────────────────────┤
│  Conversations        │   assistant: ...                    │
│  + New                │                                     │
│  Today                │   [1] DIN-18065.pdf · p. 3          │
│   · Lieferfristen     │   [2] Angebot-2024-09.docx · §2.1   │
│                       │                                     │
│  Yesterday            │                                     │
│   · Prozesshandbuch   │                                     │
│                       │                                     │
│                       ├────────────────────────────────────┤
│                       │  ▸ Type your question…         ↵  │
└───────────────────────┴────────────────────────────────────┘
```

- **Language toggle** (top right): switches system-prompt language. The model will still answer in the language you ask in; the toggle only affects *refusals* and certain system strings.
- **Conversation list** (left): your prior chats. Conversations are **private to your account**; your admin does not see them by default (audit log retains metadata only).
- **Citations** ([1], [2], …): click to preview the source. Each citation shows file name, page, and a short excerpt. Click again to open the full file (respects your permissions).

---

## 4. Asking good questions — a field guide

Reineke-RAG is not a search engine; it is an assistant that reads documents for you. Better questions → better answers.

### 4.1 Four question types, each with a pattern

| Type | Good pattern | Example (DE) | Example (EN) |
|------|--------------|--------------|--------------|
| **Lookup** | short, specific terms | "Welche Norm gilt für Typ-B-Schränke?" | "Which standard applies to type-B cabinets?" |
| **Extraction** | "List/extract all X from Y" | "Liste alle Lieferfristen aus *Angebot-2024-09.pdf*." | "Extract all delivery deadlines from *Angebot-2024-09.pdf*." |
| **Table / numeric** | mention the file or metric | "Welches Projekt in *Projekte2024.xlsx* hatte die höchste Marge?" | "In *Projects2024.xlsx*, which project had the highest margin?" |
| **Synthesis** | "Summarise / compare across…" | "Fasse unsere Position zu Thema X aus allen QMS-Dokumenten zusammen." | "Summarise our position on topic X across all QMS documents." |

### 4.2 Tips that really help

- **Name the file** if you know it. The retriever is good, but a file name is a cheat code.
- **Name the folder** if you remember only roughly where it came from. ("in unseren QMS-Dokumenten…")
- **Use product codes and proper names verbatim.** Reineke-RAG has keyword matching (sparse retrieval) — `KR-4711-B` is more useful to it than "the 4711 part".
- **Prefer one question per message** for tricky topics. You can always follow up.
- For **table math**, explicitly ask for the calculation: "highest", "sum", "average of column X where Y > 10". The system will write a SQL query behind the scenes.

### 4.3 Patterns to avoid

- "What do you think about…" — it won't. It reports what documents say.
- "Tell me everything we know about…" — too broad; the retriever will surface a noisy mix. Narrow it down.
- "Go to the internet and…" — no internet access, by design.

### 4.4 Follow-ups

The system remembers the current conversation. So after:
> *"Welche Norm gilt für Typ-B-Schränke?"*
you can ask:
> *"Und welche Mindestanforderung an die Tiefe?"*
and it will understand you're still talking about Typ-B-Schränke. If you want a **fresh** context, click **+ New** top left.

---

## 5. Reading an answer correctly

Every claim *should* have a bracketed citation. If you see:

> "Die Norm DIN 18065 regelt die Abmessungen [1]. Der Mindestwert beträgt 800 mm [2]."

Then [1] and [2] are independently clickable. Check them if the claim is load-bearing for a decision.

**Rules of thumb:**

- **No citation ≠ no source.** Occasionally the model will cite once at the end of a paragraph. Click it to verify.
- **If the cited preview does not support the claim, the answer is wrong.** Report it to the admin (see §8). The audit log makes this traceable.
- **Numbers must come from tables.** If Reineke-RAG tells you a number in a table-math answer, the system also shows the SQL it ran — you can verify by expanding the "Show SQL" panel.

---

## 6. Uploading documents

**Only users in groups with upload rights can upload.** Ask your admin if "Upload" isn't visible.

1. Click **Upload** in the left sidebar.
2. Choose the **target folder** (e.g. `/qms/normen`). Only folders your group can write to are listed.
3. Drag files in. Supported: `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls` plus `.pptx`, `.html`, `.md` when the admin has enabled them.
4. Click **Ingest**. The file appears in a "In Progress" list; status updates live (`queued → parsing → embedding → indexed`).
5. Once *indexed*, the file is searchable. Typical time: 10 s per page of a text PDF; 30 s per page of a scanned PDF (OCR); a few seconds for XLSX.

**Do not upload secrets** (passwords, private keys). The system logs queries — your future questions about this file land in an audit trail. Use folder ACLs to restrict access, and prefer a secrets manager for actual secrets.

### 6.1 What happens to my file?

- The **original** is stored unchanged inside the system (object store, versioned).
- A **parsed + chunked + embedded** copy is created for search. Chunks are small pieces (~½ page) used only internally.
- Your file **does not leave the company**. There is no external API call at any point.

### 6.2 Deleting a document

Click the trash icon in the file list → confirm. By default, deletion is a **soft delete** — the file is marked `superseded`, removed from the search index, but the bytes remain for 30 days for audit/recovery. A hard delete is an admin action.

---

## 7. Permissions — what can I see?

- Every document lives under a **folder path**. Each folder has a list of **groups** allowed to read it.
- You are in one or more groups (typical: `engineering`, `sales`, `qms`, …). You see a document only if **any one** of your groups is allowed on its folder.
- This is enforced at retrieval time: if you ask about a document you cannot read, you will get an **"information not available"** style answer — not even a hint that the document exists.
- If you believe you should have access to a folder, ask your admin. Access changes propagate within a minute.

---

## 8. Troubleshooting — user edition

| Symptom | Likely cause | What you can do |
|---------|--------------|-----------------|
| "I can't find a document I know exists." | Folder ACL doesn't include your group. | Ask admin. |
| "Answer is wrong, but cited." | Retrieval surfaced a chunk that looks related but isn't; or document contradicts itself. | Rephrase with the file name; or mention a specific section. Report repeated failures. |
| "No citation shown." | Rare — the model omitted them. | Rerun the question. If it repeats, report it. |
| "Answer is in German but I asked in English." (or vice versa) | Model language preference. | Add "Answer in English." to your question; or switch the DE/EN toggle. |
| "Table answer seems off." | SQL path misread a column. | Expand "Show SQL" and verify the column names — tell the admin if the column mapping is wrong. |
| "The page takes forever." | A heavy query (synthesis) can take up to a minute. | Use smaller questions where possible; watch the streaming answer — it starts appearing before it's finished. |
| "I uploaded a file; nothing happened." | Queue is busy or parser errored. | Check the status column; if it says `failed` click the info icon for the reason; contact admin. |

When reporting to the admin, **include the conversation link** (copy URL) and — if possible — the exact answer text. The audit log lets the admin reconstruct the query end-to-end.

---

## 9. Privacy & audit

- **What is logged for every query:** your user id, timestamp, query text, which documents were retrieved, which LLM answered, latency, token counts, a hash of the answer.
- **What is NOT logged:** your **conversations** are private to your account in the UI; admins can view the audit log (metadata) but not conversation history by default. Your org may adjust this — ask your admin for the data-protection notice.
- **Retention:** audit log retention per company policy (default 180 days).
- **Your rights (GDPR / BDSG):** you can request a copy of your audit records and have them erased, subject to legal retention duties. Ask your data protection officer.

---

## 10. Frequently asked questions

**Q: Can I use Reineke-RAG to write a summary I paste to a customer?**
Yes, but double-check the citations and facts before sending. You are accountable for outgoing communication.

**Q: Can it read a file I just attached to this chat?**
No. Files enter the system only via the **Upload** route and are scoped to a folder with an ACL. Chat attachments are intentionally not supported in v1 — they would bypass ACLs.

**Q: Why does it sometimes say "I don't have information on that"?**
Because it didn't find a supporting chunk in any document you can access. This is usually correct behaviour, not a bug — prefer "no answer" over a made-up one.

**Q: Why does my answer look the same as my colleague's but we have different citations?**
Different group memberships mean different documents are accessible — answers are built from what each user is allowed to read.

**Q: What languages does it speak?**
German and English are fully supported. Other languages may work (the embedding model covers 100+), but are not evaluated in v1.

**Q: Can I ask it to translate?**
Yes — "Translate §2.1 of *Prozesshandbuch.pdf* to English." It will cite the source and translate. For legal documents, a human translator is still advisable.

**Q: Does it get smarter over time?**
Retrieval does not learn from past queries in v1 (by design, for privacy). The admin can tune prompts and rerank weights based on the eval set; this shows up as silent quality improvement.

---

## 11. A short script: "my first good question"

1. Pick a concrete document you remember: for example, `Angebot-2024-09.pdf`.
2. Ask: *"Liste alle Lieferfristen aus Angebot-2024-09.pdf."*
3. Inspect the citations. Click one; confirm the excerpt matches the claim.
4. Follow up: *"Für welche Position ist die Frist am kürzesten und warum?"*
5. Open the citation; see whether the "why" is actually in the document (it should be — otherwise flag it).

If this works, you have the skill. Apply it to your real work.

---

## 12. Getting help

- Admin contact: written in the **ℹ About** panel in the UI (populated by your admin).
- Status page (infra health): `https://rag.<company>.local/grafana/d/overview`. Admins have links.
- For bugs or wishes: use whatever internal ticket system your company uses; include the URL of the offending conversation.
