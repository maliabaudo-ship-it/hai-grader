# HAI Corpus Quality Grader — Project Guide

This project grades `.docx` files against HAI document acceptance criteria.
It is rule-based (no LLM calls) and produces a 0–100 score with a grade and recommendation.

## Quick start

```bash
pip install -r requirements_hai_grader.txt

# Local folder of .docx files
python3 hai_grader.py --local-dir /path/to/docs

# Google Drive folder (requires credentials.json from Google Cloud Console)
python3 hai_grader.py --folder-id DRIVE_FOLDER_ID --recursive

# Common flags
--no-bundle       skip cross-document bundle bonus
--min-score 40    only output docs scoring >= 40
--output out.csv  save results to CSV
--json out.json   also save JSON
```

## Scoring rubric (max 100)

| Component | Max | What it measures |
|---|---|---|
| Structure | 40 | TOC, footnotes, cross-refs, headers/footers, tables, images, layout features |
| Polish | 25 | Heading hierarchy, style variety, document properties, no quality-degrading flags |
| Substance | 20 | Word count, structural density bonus |
| Occupation | 15 | How strongly the doc matches a known professional domain |
| Penalties | −up to 50 | PII, meeting notes, simple letters, synthetic content, PDF conversion |

### Grade bands

| Score | Grade | Recommendation |
|---|---|---|
| 75–100 | A | ACCEPT |
| 55–74 | B | ACCEPT — review recommended |
| 40–54 | C | BORDERLINE — human review needed |
| 20–39 | D | LIKELY REJECT |
| 0–19 | F | REJECT |

Hard-reject flags (PII, meeting notes, synthetic content) force REJECT regardless of score.

## Document types and expected score ranges

Calibrated against 31 real-world docs (World Bank reports, Finance/PE samples).

| Document type | Expected range | Notes |
|---|---|---|
| Investment writeup / IC memo (full) | 62–95 | High table + footnote density; memo header bonus |
| Market study / research report | 60–70 | Varies by length and structure |
| Fund / company overview memo | 60–70 | Often no TOC; score boosted by visual richness |
| Deal teaser | 45–55 | Short by design; borderline is expected |
| Industry research note | 40–50 | Image-heavy, minimal table structure |
| Brief market memo (< 1,500 words) | 33–45 | Legitimately short working memos; human review required |
| Simple letter / brief memo | < 15 | Penalised as `simple_letter` |
| Meeting notes / minutes | < 30 | Hard-penalised; most score REJECT |

## Occupation detection

The grader detects: **Finance**, **Software Engineering**, **Data Science**, **Consulting**, **Legal**.

**Confidence tiers:**
- ≥ 0.60 → 15 pts | ≥ 0.35 → 10 pts | ≥ 0.15 → 5 pts | ≥ 0.05 → 2 pts | < 0.05 → 0 pts

**Confidence formula:** Competitors are weighted at 50% so that boilerplate legal disclaimers in a Finance deal teaser don't dilute the Finance confidence score. Formula: `(best / (best + others×0.5)) × min(best/15, 1)`.

**Known false positives to watch for:**
- Deal teasers accumulate Legal hits from standard disclaimer boilerplate (warranty, liability, jurisdiction) — mitigated by the 50% competitor weighting
- Energy/infrastructure Finance docs use "pipeline" in the physical sense — added to Finance pattern alongside SE
- Project codenames that match occupation keywords (e.g. "Sprint" for a coal deal) — removed the most ambiguous single-word terms from SE pattern
- Very short memos (< 1,500 words) may score low Finance confidence even with dense Finance content

## Known grader limitations

### 1. Heading styles — Finance/PE blind spot
Finance and PE documents routinely use direct character formatting (bold, color, font size) for section headers rather than Word's built-in `Heading1` / `Heading2` styles. The grader cannot detect these visually-formatted headers.

**Mitigation in place:** Three patterns qualify for a 4-pt "visual structure" heading proxy:
- ≥ 5 tables AND ≥ 3 images (dense reports)
- ≥ 1 table AND ≥ 6 images (exhibit-heavy memos)
- ≥ 10 images AND ≥ 800 words (image-heavy research without tables)

**Implication:** A BORDERLINE (C) score for a Finance deal teaser or IC memo is often a real accept. Apply domain judgment.

### 2. PDF-to-Word conversions
Detected when `defined_style_count < 4` and `word_count > 400`. These receive −10 penalty. Real Finance docs sometimes also have low defined-style counts but are NOT conversions — check manually if flagged.

### 3. Decorative images in templates
Low-word-count docs (< 600 words) with ≥ 2 images have their raw image score capped at 1 point to prevent logos/checkboxes from inflating structure scores.

### 4. Bundle bonus
The bundle analyzer awards up to +10 points to docs in a folder with ≥ 3 style-similar files (Jaccard similarity ≥ 0.4). Finance docs from the same firm often share a template and qualify. Use `--no-bundle` to disable.

## Quality checks — what fires and why

| Flag | Penalty | Trigger |
|---|---|---|
| `meeting notes detected` | −15 | Strong: "meeting minutes", "minutes of meeting". Weak (2+ needed): "called to order", "roll call", "in attendance", "attendees:", "quorum", "agenda item", "safety meeting", "today's meeting", etc. |
| `simple letter/brief memo` | −10 | 2+ of: "dear ", "sincerely,", "regards,", "yours truly" AND word_count < 700 |
| `PII detected` | −20 | SSN pattern, credit card number, DOB label, > 5 distinct real email addresses |
| `synthetic content` | −15 | "lorem ipsum" in body text |
| `likely PDF-to-Word conversion` | −10 | defined_style_count < 4 and word_count > 400 |
| `minimal document` | −10 | word_count < 200 AND no tables AND no images |

**Text normalization:** All phrase matching normalizes Unicode punctuation (curly apostrophes, em-dashes, etc.) to ASCII before checking. This prevents false negatives from Word's smart-quote substitution.

## Training history

The grader was calibrated across three runs against 31 real-world documents.

**Run 1 — Public documents (World Bank reports, OSHA forms, university templates)**
- Meeting notes detection was too narrow (5 specific phrases, need 2 matches); expanded to strong/weak signal split with ~15 triggers
- Unicode curly apostrophes broke phrase matching; added `_normalize()` pre-processing
- Decorative images in templates (logos, checkboxes) were inflating structure scores; capped image credit at 1pt when word_count < 600

**Run 2 — Finance/PE best practices samples (first pass, 10 docs)**
- "sprint" as a project codename was triggering Software Engineering; removed from SE keywords
- "pipeline" only in SE; added to Finance (deal pipeline, M&A, infrastructure)
- Finance docs with no Word heading styles scored 0 polish points; added visual richness proxy (4 pts)
- Expanded Finance occupation keywords: deal teaser, term sheet, LOI, pipeline, fund economics, LP/GP terms, IC memo, portfolio company, etc.

**Run 3 — Finance/PE samples (deeper calibration, same 10 docs)**
- Memo header format (To/From/Date/Re) is universal in Finance working docs but was invisible to grader; added `has_memo_header` detection (+3 polish pts)
- Visual richness threshold expanded to 3 qualifying patterns (dense reports, exhibit memos, image-heavy research)
- Occupation confidence formula was over-sensitive: adding Finance keywords to a Legal-dominated deal teaser pushed Legal below the 10pt threshold; fixed by weighting competitor hits at 50%
- Removed `auc` from Data Science keywords (too ambiguous; matched energy market abbreviations)
- Near-zero occupation confidence (< 0.05) no longer awards 2 pts
- Added Consulting keywords for market studies: industry research, market study, market report, competitive analysis, industry factors, etc.
- Added Finance keywords for energy/infrastructure deals: revenue stream, deal approval, financial advisor, project economics, information memorandum, enterprise value, etc.

**Score distribution after all three runs (31 docs):**
- Full investment writeups, large reports: 62–91
- Fund memos, market reports: 60–70
- Deal teasers, working memos: 43–52
- Short industry research, brief memos: 34–44
- Meeting templates, letters, minimal docs: 0–30 (REJECT)

## How to improve the grader further

1. **More training runs:** Run against 20–30 docs per occupation type to find additional edge cases. Focus especially on Data Science and Consulting (under-represented in calibration so far).

2. **Add semantic scoring (optional flag):** A Claude API call on a 200-word excerpt could score content quality (coherence, domain depth, originality) beyond structural features. Worth adding when structural scores are insufficient to distinguish borderline cases.

3. **Human-labeled ground truth:** If you have a set of docs with known accept/reject decisions, compare against grader output to measure precision/recall and tune the grade thresholds.

4. **Adding a new occupation:**
   - Add a new entry to `OCCUPATION_PATTERNS` in `hai_grader.py`
   - Use `\b` word boundaries; prefer domain-specific terms over generic words
   - Avoid words that appear in other occupation contexts (e.g. "pipeline", "sprint", "model")
   - Test against at least 5 real docs from that occupation before shipping

## Sharing this grader

- **Code changes** → commit `hai_grader.py` to git; anyone who pulls gets the updated quality checks
- **This CLAUDE.md** → checked into the same repo; Claude Code loads it automatically so any session already knows the calibration history, limitations, and domain context
- **Skill (slash command)** → create `.claude/commands/grade-docs.md` with run instructions; anyone who clones the repo gets the `/grade-docs` skill in Claude Code
