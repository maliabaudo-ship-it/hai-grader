# Learnings — Build Honey Comb Manifest

Hard-won lessons from prior Honeycomb manifest sessions. Read before debugging.

---

## Drive MCP behavior

### File listing
- **`search_files` with `parentId` is broken** — it consistently returns only a `nextPageToken` and zero files, regardless of page size (tested at 1, 5, and 10). Do not attempt to fix this with pagination; it's a Drive MCP bug.
- **Workaround:** Search by exact title instead. Use `search_files` with `title = 'exact_filename'` for each file individually. This always works.
- Subfolders are returned alongside files in the listing when the parentId approach works. Filter by MIME type: `application/vnd.google-apps.folder` to identify subfolders, everything else is a file.
- Files inside subfolders are **NOT returned** by a top-level listing. You must explicitly list each subfolder's contents if you want recursive coverage.

### Reading file content
- `read_file_content` works for most DOCX and PPTX files under ~10MB. It returns plain text extracted from the document.
- For files over ~10MB or where `read_file_content` returns empty, use `download_file_content` instead.
- Google Docs/Slides (MIME type: `application/vnd.google-apps.document` or `...presentation`) are exported as plain text automatically by the Drive MCP. No conversion needed.
- ZIP archives return binary garbage — skip them.

### File IDs
- Always extract the Drive File ID from the API response, not from the URL. URL-based IDs are reliable for user-facing links but can include URL encoding that breaks the API.
- The pattern `/file/d/<ID>/view` works for constructing view links. Use `https://drive.google.com/file/d/<ID>/view` for all link columns.

---

## Page count extraction

### The core constraint
Bash in Cowork only has access to the connected workspace folder — it cannot reach the
CloudStorage path (`/Users/malia.baudo/Library/CloudStorage/...`). This means exact page
count extraction via `unzip` or Python is not possible in Cowork. All page counts will be
estimates; mark them with `~`.

In **Claude Code** (full filesystem access), exact counts ARE possible:
- **DOCX:** `unzip -p file.docx docProps/app.xml | grep -oP '(?<=<Pages>)\d+(?=</Pages>)'`
- **PPTX:** `unzip -l file.pptx | grep -cE "ppt/slides/slide[0-9]+\.xml"`
- **PDF:** `python3 -c "from pypdf import PdfReader; print(len(PdfReader('file.pdf').pages))"`

### Cowork estimation approach
**DOCX:** Divide word count from `read_file_content` output by 500. Mark `~`.

**PPTX:** Count `--- Slide N ---` delimiters in `read_file_content` output, add 1. Mark `~`.

**PDF:** Try `get_file_metadata` for a `pageCount` property first. If not present, estimate from content length. Mark `~`.

### Why `read_file_content` won't give you `<Pages>`
The Drive MCP returns plain extracted text, not XML. `docProps/app.xml` is never surfaced.
Do not attempt to parse `<Pages>` from `read_file_content` output — it won't be there.

---

## Naming conventions

### Industry vs. Domain
- `grade-drive-folder` uses **Domain** for naming (the 6-category Handshake grouping: Consulting, Data Science, Finance, Legal, Software Engineering, Visual).
- `build-honey-comb-manifest` uses **Industry** for naming (the specific sector). This was a deliberate change to make the manifest more informative to customers.
- Do not mix these axes between skills. If continuing numbering from an existing manifest, check which axis was used in the prior rows.

### Continuing existing numbering
- When appending to an existing sheet, scan Column A (HAI Name) for the highest number per industry.
- Parse numbers from names like `HAI_Financial Services_023.docx` → industry: Financial Services, number: 23.
- Start new docs for that industry at 24.
- If an industry appears in the new batch but has no prior rows, start at 001.

### Zero-padding
- Always use 3-digit zero-padded numbers: `001`, `002`, ..., `099`, `100`, `101`, etc.
- If a delivery ever exceeds 999 documents per industry (unlikely), extend to 4 digits.

---

## Google Sheets via Playwright

### Creating a new sheet
- Navigating to Drive and clicking "New → Google Sheets" opens Sheets in a new tab. The Playwright browser may not follow the new tab automatically — after clicking New, use `browser_tabs` to find and switch to the new tab.
- Rename the sheet immediately before entering any data, otherwise you'll be working on an "Untitled spreadsheet".
- Moving a sheet to a specific Drive folder: use the "Move to" option under File menu in Sheets, or right-click the sheet in Drive.

### Writing data in bulk
- Writing cell-by-cell via Playwright type actions is very slow for >20 rows.
- Preferred approach: build the full data as a tab-separated string, click on cell A2 (first data row), and use a single `browser_type` action to paste the entire TSV block. Sheets interprets tabs as column separators and newlines as row separators.
- Confirm the paste landed correctly by checking the first and last rows.

### HYPERLINK formula
- Formula syntax: `=HYPERLINK("https://drive.google.com/file/d/FILE_ID/view","HAI Name")`
- Requires straight double-quotes (`"`), not smart quotes (`"` or `"`).
- Do not use single quotes inside the formula — Sheets treats it as a string literal.

### Auto-resize columns
- After writing all rows: select all columns (Ctrl+A), then right-click → "Resize columns" → "Fit to data".
- In Playwright, this is easier done via the Format menu: Format → Column width → Fit to data.

---

## Classification tips

### When industry is ambiguous
- A financial services company's internal ops document → Financial Services (the customer's industry)
- A consulting firm's deliverable to a healthcare client → Healthcare (the end client's industry)
- A legal document filed in a tech patent case → Technology (the subject matter)
- When genuinely unclear, pick the industry that would make most sense to the end customer receiving this document.

### Document type gotchas
- "Slide deck" is not a document type — it's a format. Identify the genre: Pitch Deck, Board Presentation, Investor Update, Training Materials, etc.
- Internal memos and emails that were formatted as Word docs → Internal Memo or Internal Communication
- Questionnaires / surveys → Assessment or Intake Form

### TLDR quality bar
- Bad: "This document provides an overview of the company's financial performance and strategic direction for the upcoming year."
- Good: "Annual strategic plan for a mid-market SaaS company covering product roadmap, headcount growth, and $45M revenue target for FY2025."
- The TLDR should be useful to someone who has never seen the document. Include numbers, names, or specifics when available.

---

## Known edge cases

### De-identified documents with [CUSTOMER_X] tokens
- Many Honeycomb documents have had company names replaced with tokens like `[CUSTOMER_INX]` or `[CUSTOMER_D_AA]`.
- Do not try to reverse-engineer the actual company name.
- For the TLDR: use the token as-is (e.g. "Credit agreement between [CUSTOMER_D] and a syndicate of lenders.")
- For Industry: classify based on the document's content and context, not the company name.

### Files with "HAI-Scrubbed-" prefix
- Some documents were already renamed with a `HAI-Scrubbed-` prefix by a prior processing step.
- Strip this prefix when generating the HAI Name — the HAI naming convention overrides any prior prefixes.

### Very short documents (1–2 pages)
- Some files are cover letters, email chains, or single-page memos. These are still valid manifest entries.
- Page count of 1 or 2 is fine — do not flag these for review.

### Duplicate filenames
- If two files in the folder have the same name, check Drive File IDs. They are different files.
- Log a warning but process both — they'll get different HAI names.

---

## What the original 43-column CSV had that this skill drops

The prior `grade-drive-folder` workflow produced additional fields that this skill intentionally omits:
- **Pangram AI Check** (Human / AI / Mixed) — not needed for post-delivery manifest
- **Document Version** (Final / WIP) — all delivered docs should be Final
- **Domain** (Consulting / Finance / Legal) — replaced by the more specific Industry
- **Delivery Folder Link** (original folder URL) — not needed when working from a known delivered folder
- **QC error data** (Error Type, Error Text, Suggested Fix) — handled by the QC audit spec separately

If any of these are needed, switch to `grade-drive-folder` instead.
