Grade all .docx files in a Google Drive folder using the HAI corpus quality grader.

## Usage
`/grade-drive-folder https://drive.google.com/drive/folders/YOUR_FOLDER_ID`

Also accepts local file paths (zip files or directories) dropped directly into the chat.

---

## Per-user setup (edit before first run)

This skill assumes the following are already installed/configured. Set these values for your machine:

| Variable | Default for malia | What to change |
|----------|-------------------|----------------|
| `GRADER_DIR` | `/Users/malia.baudo/hai-grader` | Path to local clone of `hai-grader` repo (must contain `hai_grader.py` and `ai_screen.py`) |
| `OUTPUT_CSV` | `/Users/malia.baudo/Library/CloudStorage/GoogleDrive-malia.baudo@joinhandshake.com/Shared drives/[Confidential] Otter/Projects/Non-Safety Projects/Project Honeycomb/hai_grading_results.csv` | Where the final CSV is written. Edit for your Drive sync path. |
| `GRADER_SHEET_ID` | `1PioOYjD3rhZjme62NSG0MqiYui3Lri5l3NLhvyg2PB0` | HAI Grader Reference Sheet — shared, leave as-is |
| Google Drive MCP | Tools beginning with `mcp__*__read_file_content`, `*__search_files`, `*__get_file_metadata` | Each user's MCP hash is unique. Use `ToolSearch` with `"select:read_file_content,search_files,get_file_metadata"` to load whichever tools your install exposes. |

Sessions write to `/tmp/hai_drive_session/` (created/cleaned automatically).

---

## Steps

### 0. Read the HAI Grader Reference Sheet
**Do this before every grading session, no exceptions.** The sheet is the single source of truth — memory files may be stale.

Load the Google Drive MCP tools (`ToolSearch` query `"select:read_file_content,search_files,get_file_metadata"`) if not already loaded, then read the sheet:
```
read_file_content(fileId=GRADER_SHEET_ID)
```

Review in this order:
1. **quality metrics [change]** — pre-filters and qualitative overlays that override automated scores; check for any new Format/Authenticity or Exclusion rules
2. **Quality Rubric** — current weights for all 59 metrics
3. **Grader Learnings** — read all rows with Status = **Active**; these are calibration adjustments to apply this session (e.g. +20 for Finance credit_card false positive)
4. **Score Thresholds** — confirm current A–F cutoffs

Also check the **Output Schema** tab for any column changes since the last run. If the sheet's schema differs from the 43-column schema in `project_csv_schema.md`, the sheet takes precedence — update the export script accordingly.

Note any Active learnings that affect grading decisions this session. Apply them during Steps 5–7.

### 1. Get input from the user
The user may have provided one of:
- A Google Drive folder URL (most common) → go to Step 2
- A local directory path → skip to Step 5 (already local)
- A local zip file path → skip to Step 4b
- Nothing → ask for a Drive URL or local path

### 2. Extract the folder ID
Parse the folder ID from the URL — the long alphanumeric string after `/folders/`:
- `https://drive.google.com/drive/folders/1ABC23xyz` → `1ABC23xyz`
- `https://drive.google.com/drive/u/0/folders/1ABC23xyz` → `1ABC23xyz`
- `https://drive.google.com/drive/u/1/folders/1ABC23xyz?usp=sharing` → `1ABC23xyz`

Confirm the folder is reachable and capture its title:
```
get_file_metadata(fileId=FOLDER_ID)
```
Report the folder title back to the user.

### 3. Ask the user for the local sync path

**Always ask after a Drive URL — do not try the Drive MCP's `parentId` search.** The MCP's `parentId =` query returns empty for folders inside shared drives (a known limitation, see `reference_drive_mcp_shared_drives.md`), and even for My Drive folders the local sync is faster and more reliable.

Use the `AskUserQuestion` tool with the folder title you got from Step 2:

```
AskUserQuestion(
  question="What is the local sync path to '<FOLDER_TITLE>'?",
  header="Local path",
  options=[
    {"label": "It's at ~/Library/CloudStorage/GoogleDrive-…/My Drive/<FOLDER_TITLE>", "description": "Default sync location for personal Drive folders on macOS."},
    {"label": "It's somewhere else", "description": "I'll paste the full path."},
  ]
)
```

If they pick "somewhere else," they'll type the path in the Other field. Accept any absolute path; quote it when running `ls` / `find` if it contains spaces.

Validate the path exists and contains .docx files:
```bash
ls "USER_PROVIDED_PATH"/*.docx 2>/dev/null | wc -l
```
If zero `.docx` files, ask the user to double-check the path. If many files, report the count back before proceeding.

### 4. Copy files into the session directory
Copy `.docx` files (excluding Office lock files starting with `~$`) to `/tmp/hai_drive_session/`:
```bash
mkdir -p /tmp/hai_drive_session
rsync -a --include='*.docx' --exclude='~$*' --exclude='*' "USER_PROVIDED_PATH/" /tmp/hai_drive_session/
# Belt-and-suspenders: clean up any lock files that snuck through
rm -f /tmp/hai_drive_session/~\$*
ls /tmp/hai_drive_session/*.docx | wc -l   # confirm count
```

### 4a. Fix PDF-in-.docx files — run LibreOffice conversions in parallel
Detect and convert any `.docx` files that are actually PDFs:

```bash
python3 << 'EOF'
import os
files_to_convert = []
for fname in sorted(os.listdir('/tmp/hai_drive_session')):
    if not fname.endswith('.docx'):
        continue
    path = f'/tmp/hai_drive_session/{fname}'
    with open(path, 'rb') as f:
        if f.read(4) == b'%PDF':
            files_to_convert.append(fname)
            os.rename(path, path.replace('.docx', '.pdf'))
            print(f"Flagged as PDF: {fname}")
print(f"\n{len(files_to_convert)} file(s) need conversion" if files_to_convert else "No PDF-in-.docx files found")
EOF
```

If any PDFs were flagged, convert them in parallel:
```bash
find /tmp/hai_drive_session -name '*.pdf' -print0 | \
  xargs -0 -P 4 -I{} /Applications/LibreOffice.app/Contents/MacOS/soffice \
    --headless --infilter="writer_pdf_import" --convert-to docx --outdir /tmp/hai_drive_session {}
find /tmp/hai_drive_session -name '*.pdf' -delete
```

### 4b. Extract local zip files
If the user provided a local zip path instead of a Drive URL:
```bash
mkdir -p /tmp/hai_drive_session && unzip -q -o "PATH_TO_ZIP" -d /tmp/hai_drive_session
```
Add `--recursive` when running the grader if the zip contains subdirectories.

### 4c. Handle legacy .doc files
The grader only reads .docx. Convert .doc files using macOS textutil:
```bash
for f in "USER_PROVIDED_PATH"/*.doc; do
  base=$(basename "$f" .doc)
  textutil -convert docx -output "/tmp/hai_drive_session/${base}.docx" "$f"
done
```
**Warning:** textutil conversion often strips content from legacy .doc files. After converting, check word counts before grading — if most docs are under 200 words, the conversion failed and the batch should be skipped.

### 4d. Collect Drive file IDs via title search
Per-file Drive IDs are needed for the CSV's `drive_file_id` and `google_drive_link` columns. Search by title in **batches of ~10-12 titles per query** using `or` clauses, since the `parentId` shortcut doesn't work:

```
search_files(query="title = 'FILE_A.docx' or title = 'FILE_B.docx' or … or title = 'FILE_L.docx'", pageSize=20, excludeContentSnippets=true)
```

For batches with many files (>15), spawn a sub-agent to run these searches in parallel and write results to `/tmp/hai_drive_session/drive_metadata.json`. Use this format:
```json
[{"id": "1abc...", "title": "FILE_A.docx", "createdTime": "...", "owner": "..."}]
```

**Notes:**
- Some Drive titles drop the `.docx` extension; if exact match fails, try `title = 'FILE_A'` (without extension).
- If multiple matches return, prefer the file owned by the current user, then the most recently modified.
- Use the **local filename** (with extension and any parenthesis suffix like `FILE (1).docx`) as the JSON `title` field so it matches files in `/tmp/hai_drive_session/`, even if Drive's actual title differs.
- If a file cannot be found, leave its `id` empty — the CSV will fall back to a blank Drive link.

### 5. Run AI screen and grader concurrently
Both read the same local directory and don't conflict — start the screener in the background, then run the grader.

```bash
python3 GRADER_DIR/ai_screen.py \
  --local-dir /tmp/hai_drive_session \
  --json /tmp/hai_drive_session/ai_screen.json &
AI_SCREEN_PID=$!

python3 GRADER_DIR/hai_grader.py \
  --local-dir /tmp/hai_drive_session \
  --json /tmp/hai_drive_session/results.json

wait $AI_SCREEN_PID
```

The screener checks each `.docx` for:
- **Metadata signals**: revision count of 0 or 1, created ≈ modified timestamps, generic/blank author, non-Word application in app properties
- **XML structure signals**: ≥75% of substantive paragraphs are single-run `<w:r>` elements, zero spell-check markers on long documents
- **Content signals**: ≥3 AI boilerplate phrases (e.g. "it is worth noting", "plays a crucial role", "delve into", "in the realm of")

Confidence levels:
| Level | Meaning |
|-------|---------|
| HIGH | Multiple strong structural signals — almost certainly AI-generated |
| MEDIUM | One strong or two moderate signals — worth opening and checking |
| LOW | Single weak signal — flag for awareness, do not auto-reject |

**What to do with flagged files:**
- **HIGH**: Open the document and check for uniform paragraph structure, no typos, formulaic transitions. If confirmed, note `ai_generated` in `reviewer_notes` and skip.
- **MEDIUM**: Grade normally but note the flag — if the grader also gives a low substance score, the combination is strong evidence to skip.
- **LOW**: Grade normally, ignore the flag unless other signals agree.

The screener does **not** hard-reject anything — final call is always human.

#### Subdirectory grading:
```bash
python3 GRADER_DIR/hai_grader.py --local-dir /tmp/hai_drive_session --recursive --json /tmp/hai_drive_session/results.json
```

#### Disabling the credit card PII false positive:
Finance documents frequently trigger a false `credit_card` PII flag due to the regex matching 16-digit financial reference numbers, deal IDs, or table values. This applies a **-20 point penalty** that does not reflect real PII. When grading Finance docs (PEVC reports, investment memos, equity research), patch the grader temporarily:

```bash
cp GRADER_DIR/hai_grader.py GRADER_DIR/hai_grader.py.bak
# Comment out the credit card check at line ~292, then run grader
# Restore afterward:
cp GRADER_DIR/hai_grader.py.bak GRADER_DIR/hai_grader.py && rm GRADER_DIR/hai_grader.py.bak
```

When reporting scores for Finance docs with credit_card flags, always add +20 to the displayed score for the true adjusted score.

### 6. Spawn metadata enrichment agents
The grader doesn't know what each doc *is* — it only scores structure/polish/substance. Claude must fill `document_type`, `tags`, `tldr`, `industry` for every row by reading the actual content.

For batches >10 docs, split into batches and spawn 3 parallel sub-agents (each handling ~⅓ of the files). Each agent should:
1. Read each .docx locally via `python3 -c "import zipfile, re; ..."` from `/tmp/hai_drive_session/<fname>` — **prefer local reading over Drive reading** since Drive may have different content (versioned drafts).
2. Determine:
   - **document_type**: specific label (e.g. "Investment Committee Memo", "Stock Pitch", "PRD", "Appellate Brief", "Software Design Doc") — never generic "Report"
   - **tags**: 4–8 comma-separated keywords including named entities
   - **tldr**: 1–2 sentences with named entity to distinguish from similar docs
   - **industry**: Finance / Legal / Consulting / Data Science / Software Engineering / Other
3. Save to `/tmp/hai_drive_session/metadata_batchN.json` as a JSON list:
   ```json
   [{"file_name": "...", "document_type": "...", "tags": "...", "tldr": "...", "industry": "..."}]
   ```

**Always spot-check the agent output against actual file content for 2-3 files** — agents occasionally swap entries between near-duplicate files (e.g. multiple docs in the same case docket). Spot-check by reading the local content yourself.

### 7. Export CSV with ownership flags
After grading and metadata enrichment, export results to `OUTPUT_CSV`.

**Schema: exactly 43 columns in this order** (see `project_csv_schema.md` for full reference):
`drive_file_id, file_name, submitter_name, google_drive_link, R2 Review Results, client_facing_name, migrated, document_type, tags, tldr, industry, occupation, structure_score, polish_score, substance_score, occupation_score, penalty, bundle_bonus, total_score, grade, recommendation, page_count, word_count, table_count, image_count, Slang/Informal Language, Formatting Notes, Formality Rank, ownership_concern, copyright_safe, flags, strengths, heading_levels, quality_flags_change, error, ai_check, Verdict, Creator, Last Modified By, Editing Time (min), Signals, pangram_ai_check, spelling_and_grammar`

**Grader fills**: cols 1–4, 12–25, 31–35
**Claude fills (after manual review of content)**: cols 8–11 (document_type, tags, tldr, industry), 29–30 (ownership_concern, copyright_safe), 34 (quality_flags_change), 36 (ai_check from screener)
**Leave empty**: cols 5–7, 26–28, 37–43

```bash
python3 << 'EOF'
import csv, re, json, os, zipfile
from xml.etree import ElementTree as ET

# ── Paths ─────────────────────────────────────────────────────────────────────
SESSION         = '/tmp/hai_drive_session'
results_json    = f'{SESSION}/results.json'
drive_meta_path = f'{SESSION}/drive_metadata.json'
form_lookup_path= f'{SESSION}/form_lookup.json'
ai_screen_path  = f'{SESSION}/ai_screen.json'
out_csv         = 'OUTPUT_CSV'   # ← edit OUTPUT_CSV at top of this skill

# Canonical 43-column schema — order is fixed, do not change
FIELDNAMES = [
    'drive_file_id', 'file_name', 'submitter_name', 'google_drive_link',
    'R2 Review Results', 'client_facing_name', 'migrated',
    'document_type', 'tags', 'tldr', 'industry', 'occupation',
    'structure_score', 'polish_score', 'substance_score', 'occupation_score',
    'penalty', 'bundle_bonus', 'total_score', 'grade', 'recommendation',
    'page_count', 'word_count', 'table_count', 'image_count',
    'Slang/Informal Language', 'Formatting Notes', 'Formality Rank',
    'ownership_concern', 'copyright_safe', 'flags', 'strengths',
    'heading_levels', 'quality_flags_change', 'error', 'ai_check',
    'Verdict', 'Creator', 'Last Modified By', 'Editing Time (min)', 'Signals',
    'pangram_ai_check', 'spelling_and_grammar'
]

with open(results_json) as f:
    docs = json.load(f)

# Drive metadata (file title → {id, createdTime})
meta_by_title = {}
if os.path.exists(drive_meta_path):
    with open(drive_meta_path) as f:
        for m in json.load(f):
            meta_by_title[m['title']] = m

# Form lookup (drive_file_id → form response fields), optional
form_by_id = {}
if os.path.exists(form_lookup_path):
    with open(form_lookup_path) as f:
        form_by_id = json.load(f)

# AI screen results (file_name → {flagged, confidence})
ai_by_name = {}
if os.path.exists(ai_screen_path):
    with open(ai_screen_path) as f:
        for entry in json.load(f):
            ai_by_name[entry['file_name']] = entry

# Manual metadata from sub-agents (file_name → {document_type, tags, tldr, industry})
manual_meta = {}
for batch_file in sorted(os.listdir(SESSION)):
    if batch_file.startswith('metadata_batch') and batch_file.endswith('.json'):
        with open(f'{SESSION}/{batch_file}') as f:
            for entry in json.load(f):
                manual_meta[entry['file_name']] = entry

def parse_submitter(filename):
    stem = re.sub(r'\.docx$', '', filename, flags=re.IGNORECASE)
    stem = re.sub(r'_\d+$', '', stem)
    parts = stem.rsplit(' - ', 1)
    return parts[1].strip() if len(parts) == 2 else ''

def get_page_count(filename):
    path = os.path.join(SESSION, filename)
    try:
        with zipfile.ZipFile(path) as z:
            try:
                with z.open('docProps/app.xml') as f:
                    tree = ET.parse(f)
                    ns = {'ep': 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'}
                    pages = tree.find('.//ep:Pages', ns)
                    if pages is not None and pages.text:
                        return int(pages.text)
            except KeyError:
                pass
            # Fallback for Google Doc exports: count lastRenderedPageBreak + 1
            try:
                with z.open('word/document.xml') as f:
                    content = f.read().decode('utf-8', errors='ignore')
                breaks = content.count('lastRenderedPageBreak')
                if breaks > 0:
                    return breaks + 1
            except KeyError:
                pass
    except Exception:
        pass
    return ''

HIGH_PATTERNS = ['investment committee', ' ic memo', ' cim', 'term sheet', 'redac', 'information memorandum', 'kingston', 'bolder']
MEDIUM_PATTERNS = ['subscription agreement', 'mou', 'funding agreement', 'uddipan', 'dbpl']

def ownership_flag(name):
    n = name.lower()
    if any(p in n for p in HIGH_PATTERNS): return 'HIGH — verify before including'
    if re.search(r'\b[A-Z]\d{6,}', name): return 'HIGH — likely proprietary internal doc'
    if any(p in n for p in MEDIUM_PATTERNS): return 'MEDIUM — open and verify'
    return ''

with open(out_csv, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction='ignore')
    writer.writeheader()
    for doc in sorted(docs, key=lambda d: -d.get('total_score', 0)):
        fname = doc['file_name']
        meta  = meta_by_title.get(fname, {})
        fid   = meta.get('id', '')
        fm    = form_by_id.get(fid, {})
        mm    = manual_meta.get(fname, {})
        ai    = ai_by_name.get(fname, {})

        # Identity
        doc['drive_file_id']     = fid or doc.get('drive_file_id', '')
        doc['google_drive_link'] = f'https://drive.google.com/file/d/{fid}/view' if fid else ''
        doc['submitter_name']    = fm.get('submitter_name') or parse_submitter(fname)
        doc['page_count']        = fm.get('form_page_count') or get_page_count(fname)

        # Manual classification (from metadata batches)
        for col in ['document_type', 'tags', 'tldr', 'industry']:
            doc[col] = mm.get(col, '')

        # Ownership / copyright — filename heuristic; OVERRIDE based on content review
        doc['ownership_concern'] = ownership_flag(fname)
        doc['copyright_safe']    = doc.get('copyright_safe', '')   # set after content review

        # Quality flags from quality metrics [change] tab review
        doc['quality_flags_change'] = doc.get('quality_flags_change', '')

        # AI screen result
        doc['ai_check'] = ai.get('confidence', '') if ai.get('flagged') else ''

        # Not Claude Grader — leave empty
        for col in ['R2 Review Results', 'client_facing_name', 'migrated',
                    'Slang/Informal Language', 'Formatting Notes', 'Formality Rank',
                    'Verdict', 'Creator', 'Last Modified By', 'Editing Time (min)',
                    'Signals', 'pangram_ai_check', 'spelling_and_grammar']:
            doc[col] = doc.get(col, '')

        # Normalize list fields to semicolon-joined strings
        for col in ['flags', 'strengths']:
            v = doc.get(col, '')
            if isinstance(v, list):
                doc[col] = '; '.join(v)

        writer.writerow(doc)

# Verify
with open(out_csv) as f:
    actual_cols = next(csv.reader(f))
assert actual_cols == FIELDNAMES, f"Column mismatch:\n  got: {actual_cols}\n  want: {FIELDNAMES}"
print(f"Exported {len(docs)} rows → {out_csv}")
print(f"Schema verified: {len(actual_cols)} columns ✓")
EOF
```

If grading a new batch (not the full corpus), use a batch-suffixed filename (e.g. `hai_grading_results_delivery6.csv`) rather than overwriting the master CSV.

### 8. Present results
Show the full grader summary output, then give a plain-English interpretation:
- Hard REJECTs (and why)
- BORDERLINE docs needing human review
- Top ACCEPTs with occupation and score
- Patterns across the batch
- Average score across all docs

### 9. Ownership concern check (content-based)
After grading, scan all ACCEPT and BORDERLINE docs by **reading their actual content**, not just filenames. The filename heuristic in Step 7 is a first pass — content trumps name. Search online for any suspicious docs before including them. Override the CSV's `ownership_concern` and `copyright_safe` columns based on what you find.

**HIGH concern — exclude without explicit authorization:**
- Investment committee (IC) memos with version markers (vF, v06, vBP) and real firm names
- Deal code names tied to real PE/VC firms (e.g. "Project X — Firm Y Term Sheet")
- CIMs (Confidential Information Memorandums) — always covered by NDA in real M&A
- Documents with internal product/part codes (e.g. `P0042332-1-H1 Rev. 2_17092024_vKIP`) — likely proprietary corporate specs
- Redacted investor updates ("`MT Update ... redac`") — real portfolio company communications
- Funding agreements and MoUs with real party names
- **Real-named PE firm fund memos** (e.g. Avante Capital SBIC III) and **real M&A IC memos with named target companies** (e.g. Rio Tinto acquisitions) — content-detected, never relying on filename alone

**MEDIUM concern — open and verify:**
- Information memorandums (IMs) for real NGOs or companies
- Subscription agreements and term sheets (could be templates or real contracts)
- Signed MSA/SOW between real entities
- Investor updates from named real startups
- Any doc with "redac" or "confidential" in filename

**LOW concern — safe to include:**
- Documents with clearly fictional ticker symbols (e.g. RNBK, CFGS, SPCM) — synthetic/anonymized training data
- Student investment fund memos (student-owned, low commercial risk)
- Academic manuscripts with institutional author attribution
- Court filings (public record)
- Anonymized templates with placeholders like "20XX" or "Client"
- Numbered course submission series (e.g. "19 — BUY Stock Pitch...")

### 10. Write learnings back to the reference sheet
After every batch, append new observations to the Google Sheet using Claude in Chrome.

**Navigate to the sheet:**
```
https://docs.google.com/spreadsheets/d/GRADER_SHEET_ID/edit
```

**Append to Grader Learnings tab** — one row per notable observation:
| Date | Source/Batch | Category | Metric Affected | Observation | Recommended Action | Status | Applied By |

Status options: `Active` / `Watch` / `Pending` / `Rejected`

Only log observations that are new or contradict existing Active rows. Do not re-log patterns already tracked as Active. Good candidates:
- Occupation misclassification patterns not already in the log
- Penalty flags that were false positives (beyond the known Finance credit_card one)
- Doc types that consistently score above/below their apparent quality
- Conversion artifacts or PDF-in-docx patterns
- Filename mislabeling (file in `Legal_*` folder but content is Consulting, etc.)

**Append to Batch Performance tab** — one row per batch:
date, folder name/source, total doc count, accept count, borderline count, reject count, average score, notes.

### 11. Clean up
```bash
rm -rf /tmp/hai_drive_session
```

---

## Score Interpretation Guide

### Thresholds
| Score | Grade | Action |
|-------|-------|--------|
| 75+ | A | Accept — strong corpus candidate |
| 55–74 | B | Accept with review — open and verify |
| 40–54 | C | Borderline — human review needed |
| 20–39 | D | Likely reject — skip unless flagged |
| <20 | F | Reject — skip |

### Component scores matter
- **Substance (max ~20)** — most reliable quality signal. Low substance = skip regardless of other scores.
- **Polish (max ~25)** — formatting maturity. Low polish with high substance = good content, bare doc (still usable).
- **Structure (max ~40)** — navigational richness (TOC, bookmarks, headings, sections). Low structure alone doesn't disqualify.
- **Occupation (max 15)** — keyword match to known fields. Score of 5 doesn't mean poor quality — can be vocabulary mismatch (e.g. ETF analysis uses market language not captured by Finance keywords).
- **Penalty** — PII flags. In Finance docs, credit_card is almost always a false positive. Add +20 to get true score.

### Grader flags to act on immediately
- `likely PDF-to-Word conversion` → skip **only if polish score is very low (≤5)**; if polish is reasonable the conversion was fine
- `minimal document` → skip, essentially empty
- `meeting notes` → skip, not corpus material
- `synthetic` → skip

### Bundle bonus
The grader awards up to **+10** (not just +5) for highly stylistically similar batches (style similarity ≥ 0.90). A batch of purpose-built or carefully curated docs from the same author will score noticeably higher graded together than individually. Always grade coherent series as a group. Mixed-industry delivery batches typically score ~0.16 similarity → no bundle bonus.

### Occupation misclassification patterns
The grader's keyword matching has known blind spots:
- ETF / market analysis → classified as Unknown or Finance with low occupation score despite valid content
- Business case studies → classified as Legal or Unknown
- Leadership / org behavior → classified as Consulting or Unknown
- Don't reject solely based on Unknown occupation — check substance score

---

## Content Type Quick Reference

### Almost always worth reviewing
- Investment memos (IC memos, deal memos)
- PEVC research reports
- Equity research / stock pitch (if substantive, not one-pagers)
- Legal opinions
- Court filings (public record, no copyright concern)
- Academic manuscripts / research papers
- Regulatory memos with analytical prose

### Almost always skip
- Software tutorials and user guides (step-by-step walkthroughs)
- Student homework assignments and exam responses
- Medical device / regulated software documentation templates
- Checklists, matrices, revision histories
- One-pagers and short pitches (<1,500 words)
- Simulation reflection reports
- Template shells / placeholder documents

### Borderline — open and check
- Case analyses (quality varies widely)
- Strategy research (depends on depth)
- Leadership / organizational behavior papers
- Business case studies (depends on school and assignment level)

---

## Copyright Quick Reference

- **Court filings** → public record, no copyright concern
- **Student work** → technically owned by the student author; check if submitted by the author or a third party
- **Published law review articles** → copyrighted by journal; check if indexed online before accepting
- **Student investment fund memos** (Bevo Partners, etc.) → student-owned, low commercial copyright risk
- **Commercial research reports** → high copyright risk; search online before accepting
- **Regulatory/compliance templates** → no copyright concern, but low corpus value

To check copyright, extract identifying text from the doc (title, author, institution) and search:
```
"[Title]" "[Author]" "[Institution]"
```

---

## Batch Combining Strategy

Combining related documents into one file can boost scores by increasing word count and enabling bundle bonus. This works well when:
- Documents are a series by the same author (e.g. PEVC Report 1, 2, 3)
- Each individual doc has genuine analytical content (substance > 15)
- Combined word count will exceed 5,000 words

**Do NOT combine when:**
- Individual docs have substance scores near 0 (empty after conversion)
- Content is template/regulatory boilerplate
- Documents are from unrelated authors or topics

---

## Notes
- Only .docx and .doc files are graded. PDFs, PowerPoints, Sheets, and CSVs are skipped automatically.
- If a file fails to copy or read, log the name and skip it rather than stopping entirely.
- Mixed-format folders (PDFs, xlsx, pptx alongside docx) are common — the grader silently ignores non-.docx files.
- Average score across a batch is a useful signal: batches averaging below ~40 are not worth individual review.
- **Drive MCP `parentId` search returns empty for folders inside shared drives.** This skill works around that by always asking the user for the local sync path. Do not retry the parentId search.
