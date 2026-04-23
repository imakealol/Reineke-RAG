# Reineke-RAG — User Handbook

> This handbook is for **you, the person who will use Reineke-RAG**: logging in, asking questions about your company's documents, reading cited answers, and — if your role permits — uploading documents.
>
> You do **not** need technical knowledge. This is a user guide. If you're an administrator looking for installation, backup, or tuning, read [TECHNICAL_HANDBOOK.md](TECHNICAL_HANDBOOK.md) instead.

---

## Contents

1. [What Reineke-RAG is — in one minute](#1-what-reineke-rag-is--in-one-minute)
2. [What it does and doesn't do](#2-what-it-does-and-doesnt-do)
3. [Logging in the first time](#3-logging-in-the-first-time)
4. [A tour of the screen](#4-a-tour-of-the-screen)
5. [Asking good questions](#5-asking-good-questions)
6. [Reading an answer (citations matter)](#6-reading-an-answer-citations-matter)
7. [Special cases: tables and numbers](#7-special-cases-tables-and-numbers)
8. [Following up in a conversation](#8-following-up-in-a-conversation)
9. [Uploading documents](#9-uploading-documents)
10. [What you can and cannot see — permissions](#10-what-you-can-and-cannot-see--permissions)
11. [Privacy and what is logged](#11-privacy-and-what-is-logged)
12. [When something goes wrong](#12-when-something-goes-wrong)
13. [Frequently asked questions](#13-frequently-asked-questions)
14. [A short exercise — your first good question](#14-a-short-exercise--your-first-good-question)
15. [Glossary](#15-glossary)
16. [Getting help](#16-getting-help)

---

## 1. What Reineke-RAG is — in one minute

Reineke-RAG is your **company's internal assistant for documents**.

You type a question. It reads the Word, PDF, and Excel files you have permission to see, finds the parts that answer you, and writes a reply — in German or English — with **clickable sources** so you can verify every claim.

It runs entirely on company hardware. **Nothing leaves the building.**

Think of it as "a colleague who has read every file you can read, and will always cite where they found something."

---

## 2. What it does and doesn't do

### It does

- Answer questions about your company's internal PDF / Word / Excel files.
- Understand German **and** English — ask in whichever feels natural.
- Show you the **source** for every claim (file name, page, a preview, a link to open the file).
- Handle spreadsheets numerically — *"which project in 2024 had the highest margin?"* produces a real answer computed from the data, not a guess.
- Respect your access rights. You only see and search the folders your group is allowed on.

### It does not

- Browse the internet. No Wikipedia, no news, no web search. Only your files.
- Write or send emails, edit calendars, or touch any other system. It is read-only.
- Modify your documents. It reads them; the originals are never changed.
- Invent. If no accessible document contains the answer, it will say so explicitly rather than guess.
- Remember you across conversations by default — each chat starts fresh unless you're in the same conversation.

### If your expectation falls outside these lines

Ask your administrator. It is likely either:

- **A planned feature** that isn't in the current version.
- **A configuration choice** your admin can change for you.
- **Intentionally off** for safety (e.g. chat file attachments, which would bypass folder permissions).

---

## 3. Logging in the first time

1. Open the URL your administrator shared. It usually looks like `https://rag.<your-company>.local`.
2. You'll be redirected to the company **single sign-on** page (branded "Authentik"). Use your normal company credentials.
3. On first login, you'll be asked to **change your password**. You may also be asked to enrol a **second factor** (authenticator app) — do it; it only takes a minute.
4. After login you land in the chat screen.

### Certificate warning on first visit?

On a freshly installed system, your browser may warn about the site certificate. This is because the system uses a **company-internal certificate authority**. Your admin will either:

- Distribute the CA certificate to your computer (after which the warning goes away), **or**
- Tell you it is safe to bypass once (only on your work machine, never on public Wi-Fi).

If you see this warning on a phone or home machine, **do not bypass it**. Ask your admin.

---

## 4. A tour of the screen

Simplified sketch:

```
┌────────────────────────────────────────────────────────────┐
│  Reineke-RAG                         [DE | EN]   [You ▾]  │
├───────────────────────┬────────────────────────────────────┤
│  Conversations        │   assistant: ...                   │
│  + New                │                                    │
│  Today                │   [1] DIN-18065.pdf · p. 3         │
│   · Lieferfristen     │   [2] Angebot-2024-09.docx · §2.1  │
│                       │                                    │
│  Yesterday            │                                    │
│   · Prozesshandbuch   │                                    │
│                       │                                    │
│                       ├────────────────────────────────────┤
│                       │  ▸ Type your question…         ↵   │
└───────────────────────┴────────────────────────────────────┘
```

### Elements

- **Conversation list (left)** — your past chats. Your conversations are **private to your account**; administrators see audit metadata (who asked what, when), not the chat transcript.
- **+ New** — start a fresh conversation with no memory of earlier ones. Use this when you change topic.
- **Language toggle (top right, DE | EN)** — switches the system messages. The system will still answer in whatever language you ask in; the toggle mainly affects refusal messages and a few UI strings.
- **Citations ([1], [2], …)** — little brackets inside the answer. Click one to open a preview with the file name, page, and a short excerpt. Click again to open the full file.
- **Input box (bottom)** — type your question; press ↵ to send. Streaming: the answer appears word by word as the system generates it.

### What you don't see (on purpose)

- A model selector. The system picks the right language model automatically based on your question type. You don't need to choose.
- A chat file-upload button. Uploads go through a separate, permission-aware route (see §9).

---

## 5. Asking good questions

Reineke-RAG is smart, but your question still matters. These patterns consistently produce better answers.

### 5.1 Four kinds of question, each with a good pattern

| Kind | Good pattern | German example | English example |
|------|--------------|----------------|-----------------|
| **Lookup** (short, specific) | Name the topic with precise terms | *"Welche Norm gilt für Typ-B-Schränke?"* | *"Which standard applies to type-B cabinets?"* |
| **Extraction** (pull a list from one file) | "Liste / Extrahiere alle X aus Y" | *"Liste alle Lieferfristen aus Angebot-2024-09.pdf."* | *"Extract all delivery deadlines from Offer-2024-09.pdf."* |
| **Table / numeric** (computation) | Name the file + what to compute | *"Welches Projekt in Projekte2024.xlsx hatte die höchste Marge?"* | *"In Projects2024.xlsx, which project had the highest margin?"* |
| **Synthesis** (across many files) | "Fasse zusammen / Vergleiche …" | *"Fasse unsere Position zu Thema X über alle QMS-Dokumente zusammen."* | *"Summarise our position on topic X across all QMS documents."* |

### 5.2 Tricks that really help

- **Name the file** if you know it. It's a cheat code — retrieval locks onto it immediately.
- **Name the folder** if you remember only roughly where it came from ("in unseren QMS-Dokumenten…").
- **Use proper names and part numbers verbatim.** The system has keyword search built in; `KR-4711-B` is better than *"the 4711 part"*.
- **Prefer one question per message.** For tricky topics, ask one thing, then follow up.
- **For numbers, ask for the calculation explicitly** — "highest", "sum", "average where X > 10". The system will write a small database query behind the scenes.

### 5.3 Patterns to avoid

- *"What do you think about…"* — it won't. It reports what documents say, not opinions.
- *"Tell me everything about…"* — too broad; you'll get a noisy mix. Narrow it.
- *"Search the web for…"* — no internet access, by design.
- *"Summarise the last five PDFs uploaded"* — the system doesn't have a sort-by-date concept for casual queries. Ask your admin if you need bulk operations.

### 5.4 Switching language

The system understands both DE and EN. If you want the **answer** in a specific language, just say so:

- *"Antworte auf Deutsch."*
- *"Answer in English."*

Or use the DE | EN toggle at the top right.

---

## 6. Reading an answer (citations matter)

Every factual claim in the answer *should* end with a bracketed number like **[1]** or **[2]**. These are **clickable citations**.

Example:

> *"Die Norm DIN 18065 regelt die Abmessungen [1]. Der Mindestwert beträgt 800 mm [2]."*

[1] and [2] are independently clickable. Clicking shows:

- The file name.
- The page or section.
- A ~240-character preview of the exact passage.
- A link to open the full file (respecting your permissions).

### Rules of thumb for reading answers

- **No citation on a claim = treat it as weak.** Sometimes the system places a citation at the end of a paragraph instead of on every sentence. Click to verify if the claim matters.
- **If the cited preview does not actually support the claim, the answer is wrong.** This is rare, but it happens. Tell your administrator — they have tools to reconstruct the query and improve the system.
- **"I didn't find information on that…"** is a **feature**, not a bug. It means nothing in the documents you can access supports an answer. Trust it. Do not try to "prompt around" the refusal.

### On language

The answer language usually matches your question. If it mismatches (you asked in German, got English), just append "Antworte auf Deutsch." — or toggle DE | EN.

### On length

The system chooses length based on the kind of question:

- Lookup → short, direct.
- Extraction → structured, often a list.
- Table-math → concise answer + (optionally) the SQL query used.
- Synthesis → longer, multi-paragraph.

You can always ask for more: *"Ausführlicher bitte."* / *"Give me more detail."*

---

## 7. Special cases: tables and numbers

When you ask about a **spreadsheet** or **a number**, the system does something different:

- It writes a small **SQL query** against the data.
- It runs that query (safely — you cannot run SQL yourself; only the system can, and only against tables you have permission on).
- The rows the query returned become part of the answer.

A quality "Show SQL" panel is usually available — click it to see the exact query used. Compare it to the file if the number looks off.

**Why this matters:** an LLM asked to add up cells directly often makes small arithmetic errors, especially around decimal separators (`,` vs `.`). The SQL path avoids that entirely.

### Typical table-math questions

- *"Wie hoch ist die Summe der Kosten im Projekt Alpha?"*
- *"Welche Position hatte die längste Lieferfrist?"*
- *"Durchschnittliche Marge pro Quartal, 2024."*
- *"How many orders over €10 000 went to supplier X in Q2?"*

**Tip:** mention the file name (`.xlsx`) whenever you can — it makes routing to the SQL path very reliable.

---

## 8. Following up in a conversation

The system remembers the current conversation. After:

> *"Welche Norm gilt für Typ-B-Schränke?"*

you can say:

> *"Und welche Mindestanforderung an die Tiefe?"*

and it will understand you're still on the same topic.

**To reset:** click **+ New** at the top left. That starts a fresh conversation with no memory. Do this whenever you change subject — it prevents the system from being biased by the previous topic.

Following up is especially powerful after a big answer:

- *"Zeig mir nur Punkt 3."*
- *"Übersetze das ins Englische."*
- *"Woher stammt diese Zahl genau?"*

---

## 9. Uploading documents

Not every user has upload rights. If you don't see **Upload** in the sidebar, your group isn't authorised. Ask your admin to grant access to a specific folder.

### 9.1 How to upload

1. Click **Upload** in the left sidebar.
2. Choose the **target folder** from the dropdown. Only folders your group is allowed to write to are listed — for example `/qms/normen`.
3. Drag files in. Supported formats: `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, plus `.pptx`, `.html`, and `.md` when the admin enables them.
4. Click **Ingest**.
5. The file appears under "In Progress" with a live status: `queued → parsing → embedding → indexed`.
6. Once **indexed**, the file is searchable. Typical timing:
   - Text PDF: ~10 seconds per page.
   - Scanned PDF (needs OCR): ~30 seconds per page.
   - Word / Excel: usually under a minute.

### 9.2 What happens to your file

- The **original** is stored unchanged in the system, versioned.
- A **parsed, chunked, searchable** copy is built for retrieval. The original is still what you see when you click a citation.
- **Nothing leaves the company.** There is no cloud call, no API upload, no telemetry.

### 9.3 Deleting a document

- Click the trash icon next to the file → confirm.
- By default this is a **soft delete**: the file is marked "superseded" and disappears from search, but the bytes are kept 30 days for audit recovery.
- A **hard delete** (permanent) is an admin-only action.

### 9.4 Replacing a document with a new version

Upload a file with the same name to the same folder. If the content has changed, a new version is created and the old one is marked `superseded`. The index uses the new version from that moment.

### 9.5 Don't upload secrets

The system logs query metadata for audit. It is designed for documents, not passwords or private keys. Use your password manager for those. Folder ACLs protect *access*, not secrecy of the document's internal content — if your XLSX contains a secret, someone authorised to read the folder can find it.

---

## 10. What you can and cannot see — permissions

Every document lives in a **folder** (a logical one, like `/qms/normen` or `/sales/angebote`). Each folder is configured with a list of **groups** allowed to read it.

### Your view of the world

- You belong to one or more groups (typical ones: `engineering`, `sales`, `qms`, `finance`, `hr`).
- You can see a document only if **at least one** of your groups is on the folder's allow-list.
- Enforcement happens at retrieval time. If you ask about something in a folder you don't have access to, you get the **"information not available"** response — **no hint that such a document exists**. This is intentional — it avoids leaking the existence of sensitive files.

### Your colleague's view may be different

Two colleagues can ask the same question and get different answers because they have different group memberships. That's working as designed, not a bug.

### Asking for new access

If you believe you should have access to a folder and don't, ask your admin. ACL changes propagate within ~1 minute.

---

## 11. Privacy and what is logged

Transparency is part of the design. Here's exactly what is and isn't kept:

### Logged (admin-visible audit trail)

- Your user id.
- Timestamp.
- The **query text** you typed.
- Which document ids the system retrieved for your query.
- Which language model answered.
- Response latency and token counts.
- A hash of the answer (for tamper-detection, not the answer text itself).

### Not logged (by default)

- Your conversation history beyond the metadata above is **private to your account** in the UI.
- Administrators can view audit metadata but not your chat transcript unless your organisation has chosen a different policy — check with your admin or your data protection officer.

### Retention

- Audit log: per company policy, typically **180 days**.
- Chat history in your account: as long as you don't delete it.
- Original documents: indefinitely, subject to your organisation's records policy.

### Your GDPR / BDSG rights

You can request:

- A copy of your audit records.
- Deletion of your audit records (subject to any legal retention duty).
- A list of conversations on your account.

Direct these to your **data protection officer**.

### The offline guarantee

No outbound network calls are made at runtime. The system was tested with the network cable unplugged; it degraded gracefully without calling anything external. Your queries, your uploads, your answers — **all stay inside the company**.

---

## 12. When something goes wrong

| Symptom | Probable reason | Try this |
|---------|-----------------|----------|
| *"I can't find a document I know exists."* | Your group is not on the folder's allow-list. | Ask admin. |
| *"Answer is wrong, but cited."* | The retrieved chunk looked related but isn't; or the document itself is inconsistent. | Rephrase; name the file or section; if it repeats, report it. |
| *"The answer has no citations."* | Rare — the model omitted them. | Ask the same question again. If still none, report it. |
| *"It answered in the wrong language."* | Model language detection. | Add "Answer in English." / "Antworte auf Deutsch." or toggle DE | EN. |
| *"A table number seems wrong."* | The SQL path may have picked the wrong column. | Expand "Show SQL", check the column names. Report mismatches. |
| *"It's taking forever."* | A synthesis question can take up to a minute on large corpora. | Watch the streaming — it starts before it finishes. Or split the question. |
| *"I uploaded a file, nothing happened."* | Queue busy or parser error. | Look at the status; if "failed", hover the info icon for the reason; contact admin. |
| *"I log in and see no documents."* | Your account may not have any groups yet. | Ask admin to add you to the right group. |

### How to report usefully to the admin

Include:

1. A **link to the conversation** (copy the URL from your browser bar).
2. The **exact question** you asked.
3. The **expected** answer vs. what you got.
4. If possible, **click a citation** and note whether the preview supports or contradicts the claim.

Your admin has tools (Langfuse, audit log) that can reconstruct the query end-to-end from the conversation URL and a timestamp.

---

## 13. Frequently asked questions

**Q: Can I use Reineke-RAG to write a reply to a customer?**
Yes — it's a useful drafting tool. But **you** are responsible for what you send. Double-check the facts and citations before any outbound communication.

**Q: Can it read a file I attach in the chat?**
No. Files enter the system only through the official **Upload** flow, which enforces folder permissions. Chat-attached files would bypass that — intentionally not supported.

**Q: Why did it refuse to answer my question?**
Because nothing in the documents you can access supports an answer. This is deliberate — the system prefers "I don't know" to making something up.

**Q: Why does my colleague's answer differ from mine on the same question?**
Different group memberships → different documents accessible → different citations → sometimes different answers. Working as designed.

**Q: Does it learn from my corrections?**
Not automatically. Retrieval does not adapt from past queries by default (privacy choice). Admins can tune prompts and reranker weights based on audit trends; improvements appear quietly.

**Q: What languages does it speak?**
German and English are fully supported and evaluated. Other languages may work (the embedding model covers 100+), but aren't formally tested.

**Q: Can it translate?**
Yes. *"Translate §2.1 of Prozesshandbuch.pdf to English."* It will cite the source and translate. For legal-grade translation, still use a human translator.

**Q: Can it summarise a whole folder?**
Yes, within reason: *"Summarise the key points from all documents under /qms/."* Very broad queries are less precise — narrower prompts are always better.

**Q: Is there a size limit on files I can upload?**
Practically yes — very large scanned PDFs take longer to OCR. Ask your admin if you're unsure.

**Q: Can I export a chat?**
Copy and paste works. Formal export is not in v1.

**Q: Is my data safe?**
Everything runs on company hardware. No cloud. Communications are encrypted (HTTPS) between your browser and the server. Access is governed by your SSO account and group memberships.

---

## 14. A short exercise — your first good question

Try this to calibrate:

1. Pick a **specific document you remember** — say `Angebot-2024-09.pdf`.
2. Ask: *"Liste alle Lieferfristen aus Angebot-2024-09.pdf."*
3. Read the answer. Click **[1]**. Does the preview match?
4. Follow up: *"Für welche Position ist die Frist am kürzesten und warum?"*
5. Open the citation for the "why" — is the reason actually in the document?

If steps 3 and 5 both validate, you have the skill. The rest is practice.

---

## 15. Glossary

- **Chunk** — a small slice of a document (½ to 1 page) that the system uses internally for search. You never see chunks directly; they're the unit behind a citation.
- **Citation** — the `[1]`, `[2]` bracketed references in the answer. Click to open the source.
- **Folder** — a logical category like `/qms/normen`. Determines who can read which documents.
- **Group** — a label (like `engineering` or `qms`) attached to your account by the admin. Used to decide what you can see.
- **Hybrid retrieval** — the system uses both semantic search and keyword search at the same time. That's why `KR-4711-B` and *"Schraubverbindung"* both work.
- **OCR** — Optical Character Recognition. Turns scanned images of text back into readable text so the system can search inside scanned PDFs.
- **Refusal** — when the system says *"I didn't find information on that…"*. It's a deliberate behaviour, not an error.
- **SQL path** — for numeric questions, the system writes and runs a small database query behind the scenes instead of asking the language model to do math.
- **SSO** — Single Sign-On. One company login for everything.
- **System prompt** — invisible instructions that tell the model to cite everything and never fabricate. You don't see it, but it shapes every answer.

---

## 16. Getting help

- **Admin contact:** usually listed in the **ℹ About** panel inside the UI; your admin fills this in during setup.
- **Status page:** a Grafana dashboard at `https://rag.<company>.local/grafana/d/overview` shows whether the system is healthy. Usually admins watch this; end users can peek.
- **Bug or feature request:** use your company's normal ticket system. Include the **URL of the offending conversation** — it makes triage much faster.
- **Data protection questions:** your **data protection officer**, not the IT admin.

---

*Welcome to Reineke-RAG. Ask good questions, check the citations, and treat a refusal as information — not a failure.*

---

**Related documents:**

- [TECH_DESCRIPTION.md](TECH_DESCRIPTION.md) — what Reineke-RAG is, technically
- [TECHNICAL_HANDBOOK.md](TECHNICAL_HANDBOOK.md) — for administrators
- [docs/03_HANDBOOK.md](docs/03_HANDBOOK.md) — the bundled end-user handbook (authoritative source for UI strings)
