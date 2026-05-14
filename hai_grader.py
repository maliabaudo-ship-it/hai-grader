#!/usr/bin/env python3
"""
HAI Corpus Quality Grader
Grades .docx files in a Google Drive folder against HAI document acceptance criteria.

QUICK START:
  1. pip install -r requirements.txt
  2. Create a Google Cloud project, enable Drive API, download OAuth credentials as credentials.json
  3. python hai_grader.py --folder-id FOLDER_ID

USAGE:
  python hai_grader.py --folder-id FOLDER_ID
  python hai_grader.py --folder-id FOLDER_ID --recursive
  python hai_grader.py --folder-id FOLDER_ID --output results.csv --json results.json
  python hai_grader.py --local-dir /path/to/docx/files   # no Drive needed
"""

import io, os, re, json, zipfile, tempfile, argparse, pickle, csv, sys, logging, unicodedata
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Tuple, Optional
from collections import defaultdict, Counter
from xml.etree import ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Optional Google API imports ───────────────────────────────────────────────
try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ── Word XML namespace helpers ────────────────────────────────────────────────
W  = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
DC = "http://purl.org/dc/elements/1.1/"
OM = "http://schemas.openxmlformats.org/officeDocument/2006/math"

def wt(tag):  return f"{{{W}}}{tag}"
def wpt(tag): return f"{{{WP}}}{tag}"

def _normalize(text: str) -> str:
    """Normalize Unicode punctuation to ASCII equivalents for reliable substring matching."""
    text = unicodedata.normalize("NFKC", text)
    # curly apostrophes/quotes → straight
    for src, dst in [("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                     ("–", "-"), ("—", "-")]:
        text = text.replace(src, dst)
    return text

# ── Occupation keyword patterns ───────────────────────────────────────────────
OCCUPATION_PATTERNS: Dict[str, str] = {
    "Data Science": (
        r"\b(eda|exploratory data analysis|model card|a/b test|a/b testing|statistical significance|"
        r"regression|classification|clustering|confusion matrix|precision|recall|f1.score|roc|"
        r"p.value|confidence interval|feature importance|data dictionary|data lineage|data quality|"
        r"data drift|machine learning|deep learning|neural network|training data|validation set|"
        r"hyperparameter|overfitting|underfitting|cohort analysis|funnel analysis|lift chart|"
        r"shapley|shap|feature engineering|model evaluation|bias.variance|cross.validation)\b"
    ),
    "Software Engineering": (
        r"\b(product requirements document|prd|architecture decision|rfc|request for comment|"
        r"api spec|openapi|swagger|microservice|deployment plan|kubernetes|docker|ci/cd|"
        r"incident report|postmortem|runbook|sop|standard operating procedure|migration plan|"
        r"schema design|endpoint|authentication|authorization|service level|sla|slo|canary deploy|"
        r"rollback plan|backlog|user story|acceptance criteria|technical design|design review|"
        r"system design|capacity planning|load testing|performance testing|release notes)\b"
    ),
    "Finance": (
        r"\b(investment memo|equity research|valuation|dcf|discounted cash flow|ebitda|irr|npv|"
        r"deal memo|due diligence|credit memo|budget variance|fp&a|financial planning|forecast|"
        r"revenue model|gross margin|operating income|balance sheet|income statement|cash flow|"
        r"capital expenditure|capex|opex|leverage ratio|debt schedule|equity|lbo|leveraged buyout|"
        r"merger|acquisition|shareholder|board update|investor update|sensitivity analysis|"
        r"scenario analysis|working capital|net present value|internal rate of return|"
        r"deal teaser|term sheet|letter of intent|loi|pipeline|fund economics|"
        r"limited partner|general partner|carried interest|management fee|moic|tvpi|"
        r"co-investment|structuring committee|investment committee|ic memo|portfolio company|"
        r"revenue stream|deal approval|financial advisor|capital advisor|"
        r"information memorandum|enterprise value|transaction value|binding offer|"
        r"project economics|indicative offer|financial projection)\b"
    ),
    "Consulting": (
        r"\b(current.state assessment|market analysis|customer analysis|business case|"
        r"target operating model|strategic roadmap|gap analysis|recommendation memo|rfp|proposal|"
        r"engagement letter|workstream|deliverable|stakeholder map|change management|"
        r"process improvement|benchmarking|kpi dashboard|okr|maturity model|swim lane|raci matrix|"
        r"initiative prioritization|program management|transformation|operating model|"
        r"voice of customer|competitive landscape|go.to.market|total addressable market|tam|"
        r"industry research|market study|market report|competitive analysis|industry analysis|"
        r"industry overview|sector analysis|sector report|industry factors)\b"
    ),
    "Legal": (
        r"\b(whereas|hereinafter|indemnif|indemnity|liability|breach of contract|warranty|covenant|"
        r"governing law|jurisdiction|arbitration|dispute resolution|plaintiff|defendant|motion|"
        r"appellate brief|complaint|legal opinion|memorandum of law|non.disclosure agreement|nda|"
        r"purchase agreement|asset purchase|stock purchase|lease agreement|license agreement|"
        r"intellectual property|patent|trademark|copyright|regulatory compliance|due diligence|"
        r"exhibit|schedule|amendment|addendum|signature block|representations and warranties|"
        r"closing conditions|force majeure|severability)\b"
    ),
}

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DocAnalysis:
    """Raw features extracted from a single .docx file."""
    file_id:     str = ""
    file_name:   str = ""
    folder_path: str = ""
    error:       str = ""

    # Structure
    has_toc:                  bool = False
    has_footnotes:            bool = False
    footnote_count:           int  = 0
    has_endnotes:             bool = False
    endnote_count:            int  = 0
    has_header:               bool = False
    has_footer:               bool = False
    has_page_numbers:         bool = False
    section_count:            int  = 1
    has_columns:              bool = False
    has_landscape_sections:   bool = False
    has_bookmarks:            bool = False
    bookmark_count:           int  = 0
    has_cross_references:     bool = False
    has_content_controls:     bool = False
    content_control_count:    int  = 0
    has_tracked_changes:      bool = False
    has_comments:             bool = False
    comment_count:            int  = 0

    # Tables
    table_count:              int  = 0
    has_merged_cells:         bool = False
    has_nested_tables:        bool = False
    has_repeated_headers:     bool = False
    max_table_rows:           int  = 0
    max_table_cols:           int  = 0

    # Visual elements
    image_count:              int  = 0
    has_captions:             bool = False
    has_equations:            bool = False
    has_embedded_objects:     bool = False

    # Styles / hierarchy
    heading_levels_used:      List[int]  = field(default_factory=list)
    used_style_names:         List[str]  = field(default_factory=list)
    defined_style_count:      int  = 0

    # Substance
    word_count:               int  = 0
    paragraph_count:          int  = 0
    page_estimate:            int  = 1

    # Document properties
    has_title_property:       bool = False
    has_author_property:      bool = False

    # Quality flags
    pii_hits:                 List[str] = field(default_factory=list)
    pii_detected:             bool = False
    is_meeting_notes:         bool = False
    is_simple_letter:         bool = False
    has_memo_header:          bool = False  # professional To/From/Date/Re format
    is_visually_rich:         bool = False  # dense tables+images as structural proxy
    is_minimal_document:      bool = False
    likely_pdf_conversion:    bool = False
    appears_synthetic:        bool = False

    # Occupation
    occupation_scores:        Dict[str, int]   = field(default_factory=dict)
    occupation:               str   = "Unknown"
    occupation_confidence:    float = 0.0


@dataclass
class DocScore:
    """Final graded result for a document."""
    # ── Identity ──────────────────────────────────────────────────────────────
    file_id:               str   = ""
    file_name:             str   = ""
    google_drive_link:     str   = ""   # https://drive.google.com/file/d/{id}/view
    folder_path:           str   = ""
    submission_date:       str   = ""   # ISO date grader ran

    # ── Classification (filled by Claude during review) ───────────────────────
    document_type:         str   = ""
    tags:                  str   = ""
    tldr:                  str   = ""
    industry:              str   = ""

    # ── Scores ────────────────────────────────────────────────────────────────
    structure_score:       float = 0.0   # max 40
    polish_score:          float = 0.0   # max 25
    substance_score:       float = 0.0   # max 20
    occupation_score:      float = 0.0   # max 15
    penalty:               float = 0.0   # negative
    bundle_bonus:          float = 0.0

    total_score:           float = 0.0

    # ── Verdict ───────────────────────────────────────────────────────────────
    grade:                 str   = "F"
    recommendation:        str   = "REJECT"
    human_review_verdict:  str   = ""    # ACCEPT / REJECT / HOLD — human override
    ownership_concern:     bool  = False
    copyright_safe:        str   = ""    # Safe / Review / Exclude

    # ── Detail ────────────────────────────────────────────────────────────────
    occupation:            str         = "Unknown"
    flags:                 List[str]   = field(default_factory=list)
    strengths:             List[str]   = field(default_factory=list)
    word_count:            int         = 0
    table_count:           int         = 0
    image_count:           int         = 0
    heading_levels:        str         = ""   # comma-separated, e.g. "1,2,3"
    quality_flags_change:  str         = ""   # issues from quality metrics [change] tab
    error:                 str         = ""


# ── Document analyzer ─────────────────────────────────────────────────────────

class DocxAnalyzer:
    """Analyzes a .docx file by directly inspecting its ZIP/XML contents."""

    def analyze(self, path: str, file_id: str = "", file_name: str = "",
                folder_path: str = "") -> DocAnalysis:
        a = DocAnalysis(file_id=file_id, file_name=file_name, folder_path=folder_path)
        try:
            with zipfile.ZipFile(path, "r") as z:
                names = z.namelist()
                if "word/document.xml" not in names:
                    a.error = "Missing word/document.xml — not a valid .docx"
                    return a
                root = ET.fromstring(z.read("word/document.xml"))
                full_text = self._text_and_substance(root, a)
                self._structure(root, a, z, names)
                self._tables(root, a)
                self._images_and_captions(root, a, names)
                self._styles(z, names, a, root)
                self._footnotes_endnotes(z, names, a)
                self._headers_footers(z, names, a)
                self._comments(z, names, a)
                self._doc_properties(z, names, a)
                self._quality_flags(a)
                self._detect_occupation(full_text, a)
        except zipfile.BadZipFile:
            a.error = "Bad ZIP — not a real .docx or file is corrupt"
        except ET.ParseError as e:
            a.error = f"XML parse error: {e}"
        except Exception as e:
            a.error = f"Unexpected error: {e}"
        return a

    # ── Text & substance ──────────────────────────────────────────────────────

    def _text_and_substance(self, root, a: DocAnalysis) -> str:
        parts, para_count = [], 0
        for para in root.iter(wt("p")):
            para_count += 1
            for t in para.iter(wt("t")):
                if t.text:
                    parts.append(t.text)
        full_text = " ".join(parts)
        a.word_count      = len(full_text.split())
        a.paragraph_count = para_count
        a.page_estimate   = max(1, a.word_count // 300)

        self._pii_check(full_text, a)

        lower = _normalize(full_text).lower()
        # Strong signals: any single phrase is enough
        _strong_meeting = ("minutes of meeting", "meeting minutes", "meeting notes",
                           "safety meeting minutes", "committee minutes")
        # Weaker signals: need 2+ to confirm
        _weak_meeting = ("attendees:", "action items", "called to order", "roll call",
                         "in attendance", "agenda item", "quorum", "meeting agenda",
                         "present:", "motion was", "seconded", "today's meeting",
                         "safety meeting", "board meeting")
        if (any(p in lower for p in _strong_meeting)
                or sum(1 for p in _weak_meeting if p in lower) >= 2):
            a.is_meeting_notes = True
        if (sum(1 for p in ("dear ", "sincerely,", "regards,",
                             "to whom it may concern", "yours truly") if p in lower) >= 2
                and a.word_count < 700):
            a.is_simple_letter = True
        if "lorem ipsum" in lower:
            a.appears_synthetic = True
        # Professional memo header: To/From/Date + Re or Subject in first 500 chars
        _memo_signals = ("to:", "from:", "date:", "re:", "subject:")
        if sum(1 for kw in _memo_signals if kw in lower[:500]) >= 3:
            a.has_memo_header = True

        return full_text

    def _pii_check(self, text: str, a: DocAnalysis):
        hits = []
        # SSN
        if re.search(r"\b\d{3}-\d{2}-\d{4}\b", text):
            hits.append("SSN")
        # Credit card
        if False:
            hits.append("credit_card")
        # Date of birth
        if re.search(r"\b(DOB|Date of Birth|Born)\s*[:\-]\s*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
                     text, re.IGNORECASE):
            hits.append("DOB")
        # Multiple distinct personal emails (>3 unique non-placeholder addresses)
        emails = re.findall(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", text)
        real   = [e for e in emails if not any(d in e.lower()
                  for d in ("example.com", "yourcompany", "domain.com", "company.com",
                             "acme.com", "test.com", "sample.com"))]
        if len(set(real)) > 5:
            hits.append(f"personal_emails({len(set(real))})")
        a.pii_hits     = hits
        a.pii_detected = len(hits) > 0

    # ── Document structure ────────────────────────────────────────────────────

    def _structure(self, root, a: DocAnalysis, z, names):
        # TOC via field code
        for instr in root.iter(wt("instrText")):
            if not instr.text:
                continue
            if "TOC" in instr.text.upper():
                a.has_toc = True
            if re.search(r"\bREF\b|\bPAGEREF\b", instr.text.upper()):
                a.has_cross_references = True

        # TOC via SDT docPart
        for sdt in root.iter(wt("sdt")):
            pr = sdt.find(wt("sdtPr"))
            if pr is None:
                continue
            for child in pr.iter():
                val = child.get(wt("val"), "")
                if "toc" in val.lower() or "table of contents" in val.lower():
                    a.has_toc = True

        # Bookmarks (skip auto-generated _Toc, _Ref prefixes)
        real_bm = [b for b in root.iter(wt("bookmarkStart"))
                   if not b.get(wt("name"), "").startswith("_")]
        a.bookmark_count = len(real_bm)
        a.has_bookmarks  = len(real_bm) > 0

        # Content controls
        sdt_count = len(list(root.iter(wt("sdt"))))
        a.content_control_count = sdt_count
        a.has_content_controls  = sdt_count > 3

        # Tracked changes
        a.has_tracked_changes = (
            len(list(root.iter(wt("ins")))) > 0
            or len(list(root.iter(wt("del")))) > 0
        )

        # Sections, columns, landscape
        sects = list(root.iter(wt("sectPr")))
        a.section_count = max(1, len(sects))
        for sect in sects:
            cols = sect.find(wt("cols"))
            if cols is not None:
                try:
                    if int(cols.get(wt("num"), "1")) > 1:
                        a.has_columns = True
                except ValueError:
                    pass
            pg = sect.find(wt("pgSz"))
            if pg is not None and pg.get(wt("orient"), "") == "landscape":
                a.has_landscape_sections = True

        # Math equations
        a.has_equations = len(list(root.iter(f"{{{OM}}}oMath"))) > 0

        # OLE / embedded objects
        a.has_embedded_objects = any(
            "oleObject" in n.lower() or n.lower().endswith(".bin") for n in names
        )

    # ── Tables ────────────────────────────────────────────────────────────────

    def _tables(self, root, a: DocAnalysis):
        tables = list(root.iter(wt("tbl")))
        a.table_count = len(tables)
        for tbl in tables:
            rows = list(tbl.findall(f".//{wt('tr')}"))
            a.max_table_rows = max(a.max_table_rows, len(rows))
            for row in rows:
                cells = list(row.findall(wt("tc")))
                a.max_table_cols = max(a.max_table_cols, len(cells))
                for cell in cells:
                    pr = cell.find(wt("tcPr"))
                    if pr is not None:
                        if (pr.find(wt("vMerge")) is not None
                                or pr.find(wt("gridSpan")) is not None):
                            a.has_merged_cells = True
                for row2 in rows[:3]:
                    tr_pr = row2.find(wt("trPr"))
                    if tr_pr is not None and tr_pr.find(wt("tblHeader")) is not None:
                        a.has_repeated_headers = True
            # nested
            for inner in tbl.findall(f".//{wt('tbl')}"):
                if inner is not tbl:
                    a.has_nested_tables = True
                    break

    # ── Images & captions ────────────────────────────────────────────────────

    def _images_and_captions(self, root, a: DocAnalysis, names):
        inline  = len(list(root.iter(wpt("inline"))))
        anchor  = len(list(root.iter(wpt("anchor"))))
        vml     = len(list(root.iter("{urn:schemas-microsoft-com:vml}shape")))
        a.image_count = inline + anchor + vml

        # Check for captions: either by style or leading "Figure/Table N" text
        for para in root.iter(wt("p")):
            ppr = para.find(wt("pPr"))
            if ppr is not None:
                ps = ppr.find(wt("pStyle"))
                if ps is not None and "caption" in ps.get(wt("val"), "").lower():
                    a.has_captions = True
                    break
            text = "".join(t.text or "" for t in para.iter(wt("t"))).strip()
            if re.match(r"^(Figure|Fig\.?|Table|Chart|Exhibit|Appendix)\s+\d", text, re.I):
                a.has_captions = True

    # ── Styles & heading hierarchy ────────────────────────────────────────────

    def _styles(self, z, names, a: DocAnalysis, doc_root):
        if "word/styles.xml" not in names:
            return
        styles_root = ET.fromstring(z.read("word/styles.xml"))
        defined = []
        for style in styles_root.iter(wt("style")):
            nm_el = style.find(wt("name"))
            nm    = nm_el.get(wt("val"), "") if nm_el is not None else style.get(wt("styleId"), "")
            defined.append(nm)
        a.defined_style_count = len(defined)

        # Heading levels actually USED in the document body
        used_levels = set()
        used_styles = set()
        for para in doc_root.iter(wt("p")):
            ppr = para.find(wt("pPr"))
            if ppr is None:
                continue
            ps = ppr.find(wt("pStyle"))
            if ps is None:
                continue
            val = ps.get(wt("val"), "")
            used_styles.add(val)
            m = re.match(r"[Hh]eading(\d)$", val)
            if m:
                used_levels.add(int(m.group(1)))
        a.heading_levels_used = sorted(used_levels)
        a.used_style_names    = sorted(used_styles)

    # ── Footnotes & endnotes ──────────────────────────────────────────────────

    def _footnotes_endnotes(self, z, names, a: DocAnalysis):
        _SEP = {"separator", "continuationSeparator", "continuationNotice"}
        if "word/footnotes.xml" in names:
            fn_root = ET.fromstring(z.read("word/footnotes.xml"))
            notes   = [f for f in fn_root.findall(wt("footnote"))
                       if f.get(wt("type"), "") not in _SEP]
            a.footnote_count = len(notes)
            a.has_footnotes  = len(notes) > 0
        if "word/endnotes.xml" in names:
            en_root = ET.fromstring(z.read("word/endnotes.xml"))
            notes   = [e for e in en_root.findall(wt("endnote"))
                       if e.get(wt("type"), "") not in _SEP]
            a.endnote_count = len(notes)
            a.has_endnotes  = len(notes) > 0

    # ── Headers & footers ────────────────────────────────────────────────────

    def _headers_footers(self, z, names, a: DocAnalysis):
        for name in names:
            if name.startswith("word/header") and name.endswith(".xml"):
                a.has_header = True
                sub = ET.fromstring(z.read(name))
                for instr in sub.iter(wt("instrText")):
                    if instr.text and "PAGE" in instr.text.upper():
                        a.has_page_numbers = True
            elif name.startswith("word/footer") and name.endswith(".xml"):
                a.has_footer = True
                sub = ET.fromstring(z.read(name))
                for instr in sub.iter(wt("instrText")):
                    if instr.text and "PAGE" in instr.text.upper():
                        a.has_page_numbers = True

    # ── Comments ─────────────────────────────────────────────────────────────

    def _comments(self, z, names, a: DocAnalysis):
        if "word/comments.xml" in names:
            cm_root = ET.fromstring(z.read("word/comments.xml"))
            a.comment_count = len(list(cm_root.iter(wt("comment"))))
            a.has_comments  = a.comment_count > 0

    # ── Document properties ───────────────────────────────────────────────────

    def _doc_properties(self, z, names, a: DocAnalysis):
        if "docProps/core.xml" in names:
            core = ET.fromstring(z.read("docProps/core.xml"))
            t = core.find(f"{{{DC}}}title")
            c = core.find(f"{{{DC}}}creator")
            a.has_title_property  = t is not None and bool(t.text)
            a.has_author_property = c is not None and bool(c.text)

    # ── Quality flags ─────────────────────────────────────────────────────────

    def _quality_flags(self, a: DocAnalysis):
        # Likely PDF-to-Word conversion: almost no named styles, heavy run-level formatting
        if a.defined_style_count < 4 and a.word_count > 400:
            a.likely_pdf_conversion = True
        # Minimal document
        if a.word_count < 200 and a.table_count == 0 and a.image_count == 0:
            a.is_minimal_document = True
        # Visually rich: Finance/consulting docs often use dense tables + charts
        # instead of Word heading styles — treat as structural proxy.
        # Three qualifying patterns:
        #   1. Classic dense report: many tables AND charts
        #   2. Memo with exhibits: at least one table AND several charts
        #   3. Image-heavy research: many charts with substantial text, no tables
        if ((a.table_count >= 5 and a.image_count >= 3)
                or (a.table_count >= 1 and a.image_count >= 6)
                or (a.image_count >= 10 and a.word_count >= 800)):
            a.is_visually_rich = True

    # ── Occupation detection ──────────────────────────────────────────────────

    def _detect_occupation(self, text: str, a: DocAnalysis):
        lower = text.lower()
        scores = {}
        for occ, pattern in OCCUPATION_PATTERNS.items():
            scores[occ] = len(re.findall(pattern, lower))
        a.occupation_scores = scores
        best  = max(scores, key=scores.get)
        total = sum(scores.values())
        if scores[best] > 0 and total > 0:
            a.occupation = best
            # Weight competitor hits at 50% so that a few keywords from a secondary
            # domain (e.g. legal disclaimers in a Finance deal teaser) don't dilute
            # the confidence of the clearly dominant domain.
            other   = sum(v for k, v in scores.items() if k != best)
            adj_tot = scores[best] + other * 0.5
            a.occupation_confidence = min(1.0,
                (scores[best] / max(adj_tot, 1)) * min(scores[best] / 15, 1.0))
        else:
            a.occupation            = "Unknown"
            a.occupation_confidence = 0.0


# ── Scorer ────────────────────────────────────────────────────────────────────

class Scorer:
    """Converts a DocAnalysis into a DocScore."""

    def score(self, a: DocAnalysis) -> DocScore:
        import datetime
        drive_link = (f"https://drive.google.com/file/d/{a.file_id}/view"
                      if a.file_id else "")
        s = DocScore(
            file_id=a.file_id,
            file_name=a.file_name,
            google_drive_link=drive_link,
            folder_path=a.folder_path,
            submission_date=datetime.date.today().isoformat(),
            occupation=a.occupation,
            word_count=a.word_count,
            table_count=a.table_count,
            image_count=a.image_count,
            heading_levels=",".join(str(h) for h in a.heading_levels_used),
            error=a.error,
        )

        if a.error:
            s.grade          = "ERROR"
            s.recommendation = "ERROR"
            return s

        s.structure_score  = self._structure(a, s)
        s.polish_score     = self._polish(a, s)
        s.substance_score  = self._substance(a, s)
        s.occupation_score = self._occupation(a, s)
        s.penalty          = self._penalties(a, s)

        raw = s.structure_score + s.polish_score + s.substance_score + s.occupation_score + s.penalty
        s.total_score = max(0.0, round(raw, 1))
        s.grade, s.recommendation = self._grade(s.total_score, s.flags)
        return s

    # ── Structure (max 40) ────────────────────────────────────────────────────

    def _structure(self, a: DocAnalysis, s: DocScore) -> float:
        pts = 0.0

        # TOC
        if a.has_toc:
            pts += 5; s.strengths.append("has TOC")

        # References group (max 8)
        ref_pts = 0.0
        if a.has_footnotes or a.has_endnotes:
            ref_pts += 3; s.strengths.append(f"footnotes/endnotes ({a.footnote_count + a.endnote_count})")
        if a.has_cross_references:
            ref_pts += 2; s.strengths.append("cross-references")
        if a.has_bookmarks:
            ref_pts += min(3, 1 + a.bookmark_count // 5)
            s.strengths.append(f"bookmarks ({a.bookmark_count})")
        pts += min(8.0, ref_pts)

        # Headers / footers / page numbers (max 4)
        hf_pts = 0.0
        if a.has_header: hf_pts += 1.5
        if a.has_footer: hf_pts += 1.5
        if a.has_page_numbers: hf_pts += 1; s.strengths.append("page numbers")
        pts += min(4.0, hf_pts)

        # Layout features (max 4)
        lay_pts = 0.0
        if a.section_count > 2: lay_pts += 1.5; s.strengths.append(f"{a.section_count} sections")
        elif a.section_count > 1: lay_pts += 0.5
        if a.has_columns:           lay_pts += 1; s.strengths.append("multi-column layout")
        if a.has_landscape_sections: lay_pts += 1; s.strengths.append("landscape inserts")
        pts += min(4.0, lay_pts)

        # Content controls / forms (max 5)
        cc_pts = 0.0
        if a.has_content_controls:
            cc_pts += min(3, 1 + a.content_control_count // 5)
            s.strengths.append(f"content controls ({a.content_control_count})")
        if a.has_tracked_changes: cc_pts += 1; s.strengths.append("tracked changes")
        if a.has_comments:        cc_pts += 1; s.strengths.append(f"comments ({a.comment_count})")
        pts += min(5.0, cc_pts)

        # Tables (max 8)
        tbl_pts = 0.0
        if a.table_count >= 6:        tbl_pts += 6
        elif a.table_count >= 3:      tbl_pts += 4
        elif a.table_count >= 1:      tbl_pts += 2
        if a.has_merged_cells:        tbl_pts += 1; s.strengths.append("merged table cells")
        if a.has_nested_tables:       tbl_pts += 1; s.strengths.append("nested tables")
        if a.has_repeated_headers:    tbl_pts += 1
        if a.table_count > 0:
            s.strengths.append(f"{a.table_count} tables")
        pts += min(8.0, tbl_pts)

        # Visual elements (max 6)
        # Low word-count docs with many images are likely templates or forms with
        # decorative logos/checkboxes — cap their raw image credit to avoid inflation.
        vis_pts = 0.0
        if a.word_count < 600 and a.image_count >= 2:
            vis_pts += 1.0
        elif a.image_count >= 5:    vis_pts += 4
        elif a.image_count >= 2:  vis_pts += 2.5
        elif a.image_count >= 1:  vis_pts += 1
        if a.has_captions:        vis_pts += 1.5; s.strengths.append("figure/table captions")
        if a.has_equations:       vis_pts += 1;   s.strengths.append("equations/formulas")
        if a.has_embedded_objects: vis_pts += 1;  s.strengths.append("embedded objects")
        if a.image_count > 0:
            s.strengths.append(f"{a.image_count} images/figures")
        pts += min(6.0, vis_pts)

        return round(min(40.0, pts), 1)

    # ── Polish (max 25) ───────────────────────────────────────────────────────

    def _polish(self, a: DocAnalysis, s: DocScore) -> float:
        pts = 0.0

        # Heading hierarchy (max 9)
        # Finance/consulting docs often use direct formatting instead of Word heading
        # styles — award partial credit when the doc is otherwise visually dense.
        h = len(a.heading_levels_used)
        if h >= 3:        pts += 9; s.strengths.append("3+ heading levels")
        elif h == 2:      pts += 6; s.strengths.append("2 heading levels")
        elif h == 1:      pts += 3
        elif a.is_visually_rich: pts += 4; s.strengths.append("visual structure (tables+charts)")

        # Style variety (max 6)
        n_styles = len(a.used_style_names)
        if n_styles >= 15:   pts += 6; s.strengths.append(f"{n_styles} distinct styles used")
        elif n_styles >= 8:  pts += 4
        elif n_styles >= 4:  pts += 2

        # Document properties (max 4)
        if a.has_title_property:  pts += 2
        if a.has_author_property: pts += 2

        # Professional memo header (To/From/Date/Re) — common in Finance/PE docs
        if a.has_memo_header: pts += 3; s.strengths.append("professional memo header")

        # No quality-degrading flags (max 6)
        if not a.likely_pdf_conversion: pts += 3
        if not a.is_minimal_document:   pts += 3

        return round(min(25.0, pts), 1)

    # ── Substance (max 20) ────────────────────────────────────────────────────

    def _substance(self, a: DocAnalysis, s: DocScore) -> float:
        # Base from word count
        wc = a.word_count
        if wc >= 6000:        base = 20
        elif wc >= 3000:      base = 16
        elif wc >= 1500:      base = 12
        elif wc >= 600:       base = 7
        elif wc >= 200:       base = 3
        else:                 base = 0

        # Structural density bonus (rich short docs should still score well)
        density_bonus = 0.0
        if a.table_count >= 3:   density_bonus += 1.5
        if a.image_count >= 3:   density_bonus += 1.5
        if a.has_footnotes or a.has_endnotes: density_bonus += 1

        total = min(20.0, base + density_bonus)
        if wc > 0:
            s.strengths.append(f"~{wc:,} words")
        return round(total, 1)

    # ── Occupation (max 15) ───────────────────────────────────────────────────

    def _occupation(self, a: DocAnalysis, s: DocScore) -> float:
        conf = a.occupation_confidence
        if conf >= 0.6:    return 15.0
        elif conf >= 0.35: return 10.0
        elif conf >= 0.15: return 5.0
        elif conf >= 0.05: return 2.0  # weak but plausible signal
        return 0.0                     # near-zero confidence = no credit

    # ── Penalties ─────────────────────────────────────────────────────────────

    def _penalties(self, a: DocAnalysis, s: DocScore) -> float:
        pen = 0.0
        if a.pii_detected:
            pen -= 20; s.flags.append(f"PII detected: {', '.join(a.pii_hits)}")
        if a.is_meeting_notes:
            pen -= 15; s.flags.append("meeting notes detected")
        if a.is_simple_letter:
            pen -= 10; s.flags.append("simple letter/brief memo")
        if a.appears_synthetic:
            pen -= 15; s.flags.append("synthetic content (Lorem Ipsum)")
        if a.likely_pdf_conversion:
            pen -= 10; s.flags.append("likely PDF-to-Word conversion")
        if a.is_minimal_document:
            pen -= 10; s.flags.append("minimal document (low content)")
        return round(pen, 1)

    # ── Grade ─────────────────────────────────────────────────────────────────

    def _grade(self, score: float, flags: List[str]) -> Tuple[str, str]:
        # Hard reject on certain flags regardless of score
        hard_reject = any(
            "PII" in f or "synthetic" in f or "meeting notes" in f
            for f in flags
        )
        if hard_reject:
            return "REJECT", "REJECT"

        if score >= 75:   return "A", "ACCEPT"
        elif score >= 55: return "B", "ACCEPT — review recommended"
        elif score >= 40: return "C", "BORDERLINE — human review needed"
        elif score >= 20: return "D", "LIKELY REJECT"
        else:             return "F", "REJECT"


# ── Bundle analyzer ───────────────────────────────────────────────────────────

class BundleAnalyzer:
    """
    Groups documents by folder, detects style/template similarity within
    a folder, and applies bundle bonuses.
    """

    def apply_bundle_bonuses(self, scores: List[DocScore],
                              analyses: List[DocAnalysis]) -> Dict[str, dict]:
        """
        Returns per-folder bundle stats and mutates scores with bundle_bonus.
        """
        folder_groups: Dict[str, List[int]] = defaultdict(list)
        for i, s in enumerate(scores):
            folder_groups[s.folder_path].append(i)

        folder_reports = {}
        for folder, idxes in folder_groups.items():
            n = len(idxes)
            # Compute a rough style-fingerprint similarity within the folder
            style_sets = [set(analyses[i].used_style_names) for i in idxes]
            avg_jaccard = self._avg_jaccard(style_sets)
            is_bundle   = avg_jaccard >= 0.4 and n >= 3

            bonus_per_doc = 0.0
            bundle_label  = "none"
            if is_bundle:
                if 10 <= n <= 25:
                    bonus_per_doc = 10.0; bundle_label = "strong"
                elif n >= 3:
                    bonus_per_doc = 5.0;  bundle_label = "moderate"

            for i in idxes:
                if scores[i].grade not in ("REJECT", "ERROR"):
                    scores[i].bundle_bonus  = bonus_per_doc
                    scores[i].total_score   = min(100.0, round(
                        scores[i].total_score + bonus_per_doc, 1))
                    # Re-grade with bonus
                    s = scores[i]
                    if s.total_score >= 75: s.grade = "A"; s.recommendation = "ACCEPT"
                    elif s.total_score >= 55: s.grade = "B"; s.recommendation = "ACCEPT — review recommended"

            folder_reports[folder] = {
                "doc_count":      n,
                "avg_similarity": round(avg_jaccard, 3),
                "bundle_quality": bundle_label,
                "bonus_per_doc":  bonus_per_doc,
            }
        return folder_reports

    def _avg_jaccard(self, sets: List[set]) -> float:
        if len(sets) < 2:
            return 0.0
        total, pairs = 0.0, 0
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                a, b = sets[i], sets[j]
                u = len(a | b)
                total += len(a & b) / u if u else 0.0
                pairs += 1
        return total / pairs if pairs else 0.0


# ── Google Drive integration ──────────────────────────────────────────────────

def get_drive_service(credentials_file: str = "credentials.json",
                      token_file: str = "token.pickle"):
    """Authenticate and return a Drive API service object."""
    if not GOOGLE_AVAILABLE:
        raise RuntimeError(
            "Google API libraries not installed.\n"
            "Run: pip install google-api-python-client google-auth-oauthlib"
        )
    creds = None
    if os.path.exists(token_file):
        with open(token_file, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_file):
                raise FileNotFoundError(
                    f"credentials.json not found at '{credentials_file}'.\n"
                    "Download OAuth 2.0 credentials from Google Cloud Console → "
                    "APIs & Services → Credentials."
                )
            flow  = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds)


def list_docx_files(service, folder_id: str,
                    recursive: bool = False,
                    _path: str = "") -> List[dict]:
    """Return list of {id, name, folder_path} dicts for all .docx in folder."""
    results  = []
    page_tok = None
    q = (
        f"'{folder_id}' in parents and trashed = false and "
        f"mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'"
    )
    while True:
        resp = service.files().list(
            q=q, pageSize=100, fields="nextPageToken, files(id, name)",
            pageToken=page_tok
        ).execute()
        for f in resp.get("files", []):
            results.append({"id": f["id"], "name": f["name"], "folder_path": _path or folder_id})
        page_tok = resp.get("nextPageToken")
        if not page_tok:
            break

    if recursive:
        sub_q = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        page_tok = None
        while True:
            resp = service.files().list(
                q=sub_q, pageSize=100, fields="nextPageToken, files(id, name)",
                pageToken=page_tok
            ).execute()
            for folder in resp.get("files", []):
                sub_path = f"{_path}/{folder['name']}" if _path else folder["name"]
                results.extend(list_docx_files(service, folder["id"],
                                               recursive=True, _path=sub_path))
            page_tok = resp.get("nextPageToken")
            if not page_tok:
                break
    return results


def download_file(service, file_id: str, dest_path: str):
    """Download a Drive file by ID to dest_path."""
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


# ── Reporter ──────────────────────────────────────────────────────────────────

def print_summary(scores: List[DocScore], folder_reports: Dict[str, dict]):
    total   = len(scores)
    errors  = sum(1 for s in scores if s.error)
    accepts = sum(1 for s in scores if "ACCEPT" in s.recommendation)
    rejects = sum(1 for s in scores if s.recommendation == "REJECT")
    borders = total - accepts - rejects - errors

    print("\n" + "═" * 70)
    print("  HAI CORPUS QUALITY GRADER — SUMMARY")
    print("═" * 70)
    print(f"  Total documents analyzed : {total}")
    print(f"  Errors                   : {errors}")
    print(f"  ACCEPT                   : {accepts}")
    print(f"  BORDERLINE               : {borders}")
    print(f"  REJECT                   : {rejects}")

    if folder_reports:
        print("\n  FOLDER / BUNDLE ANALYSIS")
        print("  " + "─" * 60)
        for folder, info in folder_reports.items():
            print(f"  {folder}")
            print(f"    Docs: {info['doc_count']}  |  "
                  f"Style similarity: {info['avg_similarity']:.2f}  |  "
                  f"Bundle: {info['bundle_quality'].upper()}  |  "
                  f"Bonus: +{info['bonus_per_doc']:.0f}")

    print("\n  TOP 10 DOCUMENTS")
    print("  " + "─" * 60)
    top10 = sorted([s for s in scores if not s.error],
                   key=lambda x: x.total_score, reverse=True)[:10]
    for rank, s in enumerate(top10, 1):
        print(f"  {rank:2}. [{s.grade:6}] {s.total_score:5.1f}  {s.file_name[:55]}")
        print(f"       {s.occupation}  |  {s.recommendation}")

    print("\n  FLAG SUMMARY")
    print("  " + "─" * 60)
    all_flags: Counter = Counter()
    for s in scores:
        for f in s.flags:
            key = re.sub(r"\(.*?\)", "", f).strip()
            all_flags[key] += 1
    for flag, count in all_flags.most_common(10):
        print(f"  {count:3}x  {flag}")
    print("═" * 70 + "\n")


def write_csv(scores: List[DocScore], path: str):
    if not scores:
        return
    # Canonical column order matches Output Schema tab in HAI_Quality_Rubric sheet
    fieldnames = [
        "file_id", "file_name", "google_drive_link", "folder_path", "submission_date",
        "document_type", "tags", "tldr", "industry",
        "structure_score", "polish_score", "substance_score", "occupation_score",
        "penalty", "bundle_bonus", "total_score",
        "grade", "recommendation", "human_review_verdict",
        "ownership_concern", "copyright_safe",
        "occupation", "flags", "strengths",
        "word_count", "table_count", "image_count", "heading_levels",
        "quality_flags_change", "error",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for s in scores:
            row = asdict(s)
            row["flags"]     = "; ".join(row["flags"])
            row["strengths"] = "; ".join(row["strengths"])
            writer.writerow(row)
    log.info(f"CSV written → {path}")


def write_json(scores: List[DocScore], path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in scores], f, indent=2)
    log.info(f"JSON written → {path}")


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_on_drive(folder_id: str, recursive: bool,
                 credentials: str, token: str) -> Tuple[List[DocScore], List[DocAnalysis]]:
    service = get_drive_service(credentials, token)
    log.info(f"Listing .docx files in folder {folder_id} (recursive={recursive})…")
    files = list_docx_files(service, folder_id, recursive=recursive)
    log.info(f"Found {len(files)} .docx file(s)")

    analyzer = DocxAnalyzer()
    scorer   = Scorer()
    analyses, scores = [], []

    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(files, 1):
            log.info(f"[{i}/{len(files)}] {f['name']}")
            dest = os.path.join(tmp, f"{f['id']}.docx")
            try:
                download_file(service, f["id"], dest)
            except Exception as e:
                a = DocAnalysis(file_id=f["id"], file_name=f["name"],
                                folder_path=f["folder_path"], error=f"Download failed: {e}")
                s = scorer.score(a)
                analyses.append(a); scores.append(s)
                continue
            a = analyzer.analyze(dest, file_id=f["id"], file_name=f["name"],
                                  folder_path=f["folder_path"])
            analyses.append(a)
            scores.append(scorer.score(a))

    return scores, analyses


def run_on_local(directory: str) -> Tuple[List[DocScore], List[DocAnalysis]]:
    docx_files = list(Path(directory).rglob("*.docx"))
    log.info(f"Found {len(docx_files)} .docx file(s) in {directory}")

    analyzer = DocxAnalyzer()
    scorer   = Scorer()
    analyses, scores = [], []

    for i, path in enumerate(docx_files, 1):
        log.info(f"[{i}/{len(docx_files)}] {path.name}")
        folder = str(path.parent.relative_to(directory)) or "."
        a = analyzer.analyze(str(path), file_name=path.name, folder_path=folder)
        analyses.append(a)
        scores.append(scorer.score(a))

    return scores, analyses


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Grade .docx files in Google Drive against HAI corpus quality criteria.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--folder-id",  metavar="ID",
                     help="Google Drive folder ID")
    src.add_argument("--local-dir",  metavar="PATH",
                     help="Local directory of .docx files (no Drive auth needed)")

    parser.add_argument("--recursive",    action="store_true",
                        help="Recurse into sub-folders")
    parser.add_argument("--output",       default="hai_grading_results.csv",
                        metavar="FILE",   help="CSV output path (default: hai_grading_results.csv)")
    parser.add_argument("--json",         metavar="FILE",
                        help="Also write JSON output to FILE")
    parser.add_argument("--no-bundle",    action="store_true",
                        help="Skip cross-document bundle analysis")
    parser.add_argument("--credentials",  default="credentials.json",
                        metavar="FILE",   help="Google OAuth credentials file")
    parser.add_argument("--token",        default="token.pickle",
                        metavar="FILE",   help="Saved token file for re-auth")
    parser.add_argument("--min-score",    type=float, default=0.0,
                        metavar="N",      help="Only output docs scoring >= N")

    args = parser.parse_args()

    if args.folder_id:
        scores, analyses = run_on_drive(args.folder_id, args.recursive,
                                        args.credentials, args.token)
    else:
        scores, analyses = run_on_local(args.local_dir)

    # Bundle analysis
    folder_reports = {}
    if not args.no_bundle and analyses:
        ba = BundleAnalyzer()
        folder_reports = ba.apply_bundle_bonuses(scores, analyses)

    # Filter
    if args.min_score > 0:
        scores = [s for s in scores if s.total_score >= args.min_score or s.error]

    print_summary(scores, folder_reports)

    write_csv(scores, args.output)
    if args.json:
        write_json(scores, args.json)

    # Exit code: 0 if any ACCEPT, 1 if all rejected
    any_accept = any("ACCEPT" in s.recommendation for s in scores)
    sys.exit(0 if any_accept else 1)


if __name__ == "__main__":
    main()
