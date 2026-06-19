---
name: grade-drive-folder
description: >
  Full document analysis of a Google Drive folder — generates a comprehensive manifest
  (industry, domain, document type, tags, TLDR, page count, Pangram AI check, HAI naming
  convention, Drive ID) and outputs a CSV or writes to a Google Sheet manifest tab.
  Use when asked to "grade" or "analyze" a Drive folder, build a delivery manifest,
  classify documents, generate naming conventions, or audit a batch before delivery.
  Produces the complete multi-field output used in the FCP Delivery Staging spreadsheet.
---

# Grade Drive Folder

Full document cataloging pass over a Google Drive folder. Produces a comprehensive per-document
record used to populate the FCP Delivery Staging manifest and prepare batches for delivery.

## What this skill produces

For every file in the target folder (and subfolders, if instructed), it outputs:

| Column | Description |
|---|---|
| HAI Name | Canonical naming convention: `HAI_(Domain)_(zero-padded-3-digit)` e.g. `HAI_Consulting_001.docx` |
| Drive File ID | Google Drive file ID (extracted from URL or API) |
| Drive Link | Full view URL `https://drive.google.com/file/d/<id>/view` |
| Domain | High-level category matching the HAI naming prefix: Consulting, Data Science, Finance, Legal, Software Engineering, Visual |
| Industry | Specific industry sector: Financial Services, Technology, Healthcare, Legal, Education, Energy & Utilities, Real Estate, Agriculture, etc. |
| Document Type | Document format/genre: Equity Research Report, Strategic Plan Memorandum, Appellate Brief, Credit Agreement, Policy Document, Data Sheet, etc. |
| Tags | 5–8 comma-separated keyword phrases describing subject matter |
| Description | 2–3 sentence TLDR covering subject, key parties, and main content |
| Page Count | Number of pages (DOCX) or slides (PPTX) |
| Pangram AI Check | `Human`, `AI`, or `Mixed` — whether content reads as human-generated |
| Document Version | `Final File` or `WIP Iteration` |
| Delivery Folder Link | Original source URL from the input folder |

The FCP Delivery Staging manifest (ID: `1gXr9E7TbmwITT1_kPXnYbk7nGfbo7jJ594F_jvahRGg`) uses a
subset of these columns per delivery tab. Always ask the operator which output format they need
before running (CSV, new Sheet tab, or existing sheet update).

---

## Phase 1: Identify the folder

Accept input as:
- A Drive folder URL: extract the folder ID from `/folders/<id>` or the `id=` query param
- A bare folder ID
- "The folder I'm in" → check the active Chrome tab if connected

List all files using `mcp__20a04fee-c7e4-46a5-8a9f-72189e343fe9__search_files` with the
folder ID. For recursive listing, also fetch subfolder contents. Filter to document types:
`.docx`, `.pptx`, `.pdf` (Google Docs/Slides exported formats are also valid).

---

## Phase 2: Analyze each document

For each file, call `mcp__20a04fee-c7e4-46a5-8a9f-72189e343fe9__get_file_metadata` to get
the filename, MIME type, and size. Then call
`mcp__20a04fee-c7e4-46a5-8a9f-72189e343fe9__read_file_content` (or `download_file_content`)
to get the text content.

With the content in hand, determine each field:

### Domain (HAI naming prefix)
Map to one of exactly these values:
- **Consulting** — strategy, management consulting, advisory, organizational documents
- **Data Science** — analytics, ML/AI, data pipelines, research, quantitative analysis
- **Finance** — investment banking, equity research, credit, M&A, accounting, financial planning
- **Legal** — contracts, briefs, motions, policies, compliance, regulatory filings
- **Software Engineering** — technical specs, API docs, architecture, code reviews, engineering plans
- **Visual** — design briefs, brand guides, creative direction, marketing decks

When in doubt, match to the domain whose document conventions the file most resembles.

### Industry
More specific than Domain. Examples:
- Financial Services, Investment Management, Banking, Insurance, Private Equity
- Healthcare, Life Sciences, Pharmaceuticals, Medical Devices
- Technology, Software, SaaS, Cybersecurity
- Real Estate, Construction, Infrastructure
- Energy & Utilities, Oil & Gas, Renewables
- Legal Services, Compliance, Regulatory
- Education, Higher Education, EdTech
- Agriculture, Food & Beverage
- Retail, E-commerce, Consumer Goods
- Manufacturing, Industrials, Logistics

### Document Type
Identify the specific document genre, not just the file format. Examples:
Appellate Brief, Credit Agreement, Equity Research Report, IC Approval Paper,
Strategic Plan Memorandum, Discussion Agenda, Subscription Agreement, Data Sheet,
Operations Manual, Technical Specification, Policy Document, Market Analysis,
Pitch Deck, Board Presentation, Investor Update, Employment Agreement, etc.

### Tags
5–8 comma-separated keyword phrases that represent the document's subject matter,
parties, key concepts, and industry terms. Think: what would someone search for to find this?

### Description
2–3 sentences covering: (1) what the document is, (2) who the key parties or subjects are,
(3) the main content or purpose. Do not start with "This document..."

### Page Count
Files are locally synced at:
`/Users/malia.baudo/Library/CloudStorage/GoogleDrive-malia.baudo@joinhandshake.com/Shared drives/[Confidential] Otter/Projects/Non-Safety Projects/Project Honeycomb`

Use bash to extract exact counts — do not estimate from word count.

**DOCX:**
```bash
unzip -p "/path/to/file.docx" docProps/app.xml | grep -oP '(?<=<Pages>)\d+(?=</Pages>)'
```
**PPTX:**
```bash
unzip -l "/path/to/file.pptx" | grep -cE "ppt/slides/slide[0-9]+\.xml"
```
**PDF:**
```bash
python3 -c "from pypdf import PdfReader; print(len(PdfReader('/path/to/file.pdf').pages))"
```

If bash fails, fall back to word-count estimation (~500 words/page) and mark with `~`. Use `0` only if no method yields a result.

### Pangram AI Check
Assess whether the writing reads as human-generated, AI-generated, or mixed:
- **Human**: Natural variation in sentence structure, specific proper nouns, stylistic inconsistency, informal asides
- **AI**: Overly structured, formulaic transitions, unnaturally uniform paragraphs, generic phrasing
- **Mixed**: Clear sections of each

### Document Version
- **Final File**: Clean, polished, no tracked changes, no "Draft" watermarks
- **WIP Iteration**: Contains track changes, "Draft" markings, version numbers in filename (v1/v2/v3)

---

## Phase 3: Generate HAI naming conventions

After analyzing all documents, group them by **Domain**.

Within each Domain group, sort alphabetically by original filename (or by date if metadata
is available), then assign ascending zero-padded 3-digit numbers: `001`, `002`, ..., `NNN`.

For WIP Iterations of the same base document, use version suffixes: `HAI_Finance_003v1`,
`HAI_Finance_003v2`, etc. Keep version suffixes only when the original filenames explicitly
indicate multiple iterations of the same content.

Final format: `HAI_<Domain>_<NNN>.<ext>` (no spaces in Domain names, e.g. `HAI_DataScience_001.docx`,
`HAI_SoftwareEngineering_003.pptx`).

---

## Phase 4: Output

### Option A — CSV (default)
Build a CSV with the columns listed in "What this skill produces" above. Save to the workspace
folder and present with `mcp__cowork__present_files`.

### Option B — Write to existing Sheet tab
If the operator specifies a manifest spreadsheet and sheet tab name:
1. Use Playwright (`mcp__plugin_hai-operator-toolkit_playwright__browser_navigate`) to open the sheet
2. Clear existing content below the header row
3. Write the header row, then one row per document
4. Use `=HYPERLINK("url","display")` formula syntax for link columns

### Option C — Create new Sheet tab
Navigate to the target spreadsheet in Playwright, insert a new tab named after the delivery
date (e.g. "Delivery Manifest 6/18"), and populate as in Option B.

---

## Phase 5: Quality checks

After writing output:
- Confirm row count matches file count from Phase 1
- Flag any rows where Domain is ambiguous (add a note column)
- Flag any files where page count is 0 (manual check needed)
- Flag any `Mixed` or `AI` Pangram results for operator review

---

## References

See `references/learnings.md` for hard-won lessons from prior runs, including Drive MCP
quirks, MIME type handling, and naming edge cases.

---

## Example invocations

> "Grade this folder: https://drive.google.com/drive/folders/14zWJqKLy2aRKaSlsD6-ovmdowU8286dn"
> "Run a full analysis on the INT batch and export to CSV"
> "Build a delivery manifest for the June 18 folder"
