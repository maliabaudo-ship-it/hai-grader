---
name: build-honey-comb-manifest
description: >
  Build a Honeycomb delivery manifest for an already-processed Drive folder.
  Enumerates every document, generates HAI naming conventions (HAI_Industry_001),
  extracts page counts, and classifies each file with industry, document type,
  tags, and a one-line TLDR — then outputs a CSV to the local synced Drive path.
  Use when documents are already delivered and you need to catalog them, when asked
  to "build the manifest", "run the manifest skill", "catalog this folder", or
  "generate naming for these docs".
  Faster and simpler than grade-drive-folder — no Pangram check, no WIP versioning.
---

# Build Honey Comb Manifest

Catalog a delivered Drive folder and output an 8-column CSV manifest.
Documents are assumed already processed and final.

## Output columns

| Column | Description |
|---|---|
| **HAI Name** | `HAI_(Industry)_NNN.ext` — canonical name for this document |
| **Drive File ID** | Google Drive file ID |
| **Drive Link** | Plain URL: `https://drive.google.com/file/d/<id>/view` |
| **Industry** | Industry classification (see taxonomy below) |
| **Document Type** | Document genre (e.g. Equity Research Report, Appellate Brief) |
| **Tags** | 5–8 comma-separated keyword phrases |
| **TLDR** | 1–2 sentences: what is it + what is it about |
| **Page Count** | Pages (DOCX) or slides (PPTX); estimates marked with `~` |

---

## CSV output path

Write to:
`/Users/malia.baudo/Documents/Claude/Projects/Project Honeycomb/INT/Build Manifest CSVs/honeycomb_manifest_<source_name>_YYYY-MM-DD.csv`

Header row: `HAI Name,Drive File ID,Drive Link,Industry,Document Type,Tags,TLDR,Page Count`

Present the file with `mcp__cowork__present_files` when done.

---

## Phase 1: Get the file list

Accept input as a Drive folder URL, a bare folder ID, or a list of Drive file IDs.

**If given file IDs directly:** you're done — proceed to Phase 2 with those IDs.

**If given a folder URL or ID:**
Do NOT use `search_files` with a `parentId` — this is a known Drive MCP bug that returns only a pagination token and no files, at any page size.

Workaround: ask the operator to list the filenames, then search for each one individually:
```
search_files: title = 'exact_filename.docx'
```
This reliably returns the file and its Drive File ID.

Filter to: `.docx`, `.pptx`, `.pdf`, `.doc`, `.ppt`. Skip folders and zip archives.

---

## Phase 2: Analyze each document

For each file, run steps A–E.

### A. Read content
Call `mcp__20a04fee-c7e4-46a5-8a9f-72189e343fe9__read_file_content` with the Drive File ID.
This extracts readable text from DOCX and PPTX — use it as the source for classification and page count estimation.

If the file is too large (>10MB) or returns empty, use `download_file_content` instead.
If neither yields usable text, set TLDR to `[manual review needed]` and continue.

### B. Classify Industry
Pick one value from this list:

**Finance & Investment:** Financial Services · Investment Management · Private Equity · Venture Capital · Banking · Insurance · Accounting & Audit

**Legal:** Legal Services · Compliance · Regulatory · Intellectual Property

**Technology:** Software & SaaS · Cybersecurity · Data & Analytics · AI/ML · Cloud Infrastructure · Hardware & Semiconductors · Telecommunications

**Healthcare & Life Sciences:** Healthcare Systems · Pharmaceuticals · Medical Devices · Biotechnology

**Professional Services:** Management Consulting · Strategy · Human Resources · Operations

**Real Assets:** Real Estate · Construction · Infrastructure · Energy & Utilities · Oil & Gas

**Other:** Education · Retail & E-commerce · Manufacturing · Logistics & Supply Chain · Food & Agriculture · Media & Entertainment · Government & Public Sector

If cross-sector, pick the primary audience (e.g. software pitch deck to a bank → Financial Services).

### C. Classify Document Type
Examples: Appellate Brief · Credit Agreement · Equity Research Report · IC Approval Paper · Strategic Plan · Pitch Deck · Board Presentation · Investor Update · Employment Agreement · Training Materials · Statement of Work · Annual Report · Due Diligence Report · Term Sheet · White Paper · Case Study

Write a plain-language description if none of the above fit.

### D. Generate Tags
5–8 comma-separated keyword phrases. Be specific — include industry terms, key topics, entities, and what someone would search for. No generic filler like "business document."

### E. Write TLDR
1–2 sentences. Lead with the content — don't start with "This document..." Include specifics (numbers, names, parties) when available.

Examples:
- "Equity research initiating coverage on Meridian Bio; 12-month price target of $48 with a Buy rating."
- "Credit agreement between [CUSTOMER_D] and a syndicate of lenders for a $250M revolving facility."

### F. Extract Page Count
Page counts are estimates in Cowork — bash cannot access the CloudStorage path. Always mark with `~`.

- **DOCX:** Divide word count from the extracted text by 500. Mark `~`.
- **PPTX:** Count `--- Slide N ---` delimiters in the extracted text + 1. Mark `~`.
- **PDF:** Try `get_file_metadata` for a `pageCount` property. If absent, estimate from content length. Mark `~`.

Write `0` only if nothing yields a result.

> **Claude Code only:** If running in Claude Code (not Cowork), use bash for exact counts:
> DOCX: `unzip -p file.docx docProps/app.xml | grep -oP '(?<=<Pages>)\d+(?=</Pages>)'`
> PPTX: `unzip -l file.pptx | grep -cE "ppt/slides/slide[0-9]+\.xml"`
> Remove `~` when counts are exact.

---

## Phase 3: Generate HAI names

1. Check for an existing manifest CSV in the output folder — if one exists, read it to find the highest HAI number already assigned per industry
2. Group documents by Industry
3. Within each industry, sort alphabetically by original filename
4. Number sequentially from the next available number (start at `001` if no prior entries for that industry)
5. Format: `HAI_(Industry)_NNN.ext` — spaces in industry names are fine (e.g. `HAI_Financial Services_001.docx`)

---

## Phase 4: Write and report

Build the CSV with proper escaping (wrap fields containing commas in double-quotes). Write with the `Write` tool, then present with `mcp__cowork__present_files`.

Report:
- Row count
- Industry breakdown with HAI number ranges
- Any files flagged for manual review

---

## Known quirks

- **`search_files` with parentId is broken** — always use individual title searches
- **`read_file_content` returns empty for large files** — fall back to `download_file_content`
- **Page counts are always estimates in Cowork** — marked `~`; exact counts require Claude Code
- **Industry ≠ Domain** — this skill uses Industry (specific sector); grade-drive-folder uses Domain (Consulting/Finance/Legal)

---

## Relationship to grade-drive-folder

| | grade-drive-folder | build-honey-comb-manifest |
|---|---|---|
| Output | CSV or Sheet tab in FCP manifest | CSV to Build Manifest CSVs folder |
| Fields | 12+ columns incl. Pangram, Version | 8 columns — lean, delivery-focused |
| Naming axis | Domain (Consulting/Finance/Legal) | Industry (specific sector) |
| Use case | Pre-delivery analysis | Post-delivery cataloging |
