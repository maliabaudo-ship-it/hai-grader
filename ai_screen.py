#!/usr/bin/env python3
"""
Fast AI-generation pre-screen for .docx files.

Checks document metadata and XML structure for signals that suggest the file
was produced by an AI writing tool rather than a human working in Word.
Designed to run in seconds across batches of 50+ files before full grading.

Usage:
    python ai_screen.py --local-dir /tmp/hai_drive_session
    python ai_screen.py --local-dir /tmp/hai_drive_session --flagged-only
    python ai_screen.py --local-dir /tmp/hai_drive_session --json /tmp/hai_drive_session/ai_screen.json
"""

import argparse
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from xml.etree import ElementTree as ET

W       = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CP_NS   = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS   = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
EP_NS   = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

# Applications known to be used by programmatic doc generators
NON_WORD_APPS = ("python", "aspose", "openxml sdk", "libreoffice", "google", "pandoc", "reportlab")

# Authors that indicate no real human filled in the metadata
GENERIC_AUTHORS = {"", "user", "admin", "author", "python-docx", "aspose", "libreoffice",
                   "unknown", "owner", "writer", "document", "test"}

# Byte-level phrase scan — common in LLM output, rare in human professional writing
AI_PHRASES = [
    b"it is worth noting",
    b"it is important to note",
    b"it should be noted",
    b"in conclusion,",
    b"to summarize,",
    b"in summary,",
    b"furthermore,",
    b"moreover,",
    b"it is essential to",
    b"plays a crucial role",
    b"plays a pivotal role",
    b"delve into",
    b"in the realm of",
    b"leveraging",
    b"it is imperative",
    b"as previously mentioned",
    b"this comprehensive",
    b"a holistic approach",
    b"multifaceted",
]

# How many AI phrases must appear before we treat it as a signal
AI_PHRASE_THRESHOLD = 3


@dataclass
class ScreenResult:
    file_name: str
    flagged: bool = False
    confidence: str = "—"       # HIGH / MEDIUM / LOW / —
    signals: list = field(default_factory=list)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def screen_docx(path: str) -> ScreenResult:
    fname = os.path.basename(path)
    result = ScreenResult(file_name=fname)
    signals = []
    strong = 0  # signals that are individually highly diagnostic

    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())

            # ── 1. Core properties (metadata) ────────────────────────────────
            if "docProps/core.xml" in names:
                with z.open("docProps/core.xml") as f:
                    root = ET.parse(f).getroot()

                rev_el   = root.find(f"{{{CP_NS}}}revision")
                cre_el   = root.find(f"{{{DCTERMS}}}created")
                mod_el   = root.find(f"{{{DCTERMS}}}modified")
                auth_el  = root.find(f"{{{DC_NS}}}creator")
                lmod_el  = root.find(f"{{{CP_NS}}}lastModifiedBy")

                rev = (rev_el.text or "").strip() if rev_el is not None else ""
                if rev in ("0", "1"):
                    signals.append(f"revision={rev} — file was never edited after creation")
                    strong += 1

                cre_val = cre_el.text if cre_el is not None else None
                mod_val = mod_el.text if mod_el is not None else None
                if cre_val and mod_val:
                    if cre_val == mod_val:
                        signals.append("created == modified — single-shot write, no editing session")
                        strong += 1
                    else:
                        try:
                            delta = abs((_parse_dt(mod_val) - _parse_dt(cre_val)).total_seconds())
                            if delta < 60:
                                signals.append(f"created ≈ modified (Δ={delta:.0f}s) — written in one shot")
                                strong += 1
                        except Exception:
                            pass

                author = (auth_el.text or "").strip() if auth_el is not None else ""
                if author.lower() in GENERIC_AUTHORS:
                    signals.append(f'generic/blank author: "{author}"')

            # ── 2. App properties ─────────────────────────────────────────────
            if "docProps/app.xml" in names:
                with z.open("docProps/app.xml") as f:
                    root = ET.parse(f).getroot()

                app_el = root.find(f"{{{EP_NS}}}Application")
                app_val = (app_el.text or "").strip() if app_el is not None else ""
                if any(x in app_val.lower() for x in NON_WORD_APPS):
                    signals.append(f'non-Word application: "{app_val}"')
                    strong += 1

            # ── 3. Document XML (structure + content signals) ─────────────────
            doc_xml = next((n for n in names if n.endswith("word/document.xml")), None)
            if doc_xml:
                with z.open(doc_xml) as f:
                    raw = f.read()

                # Proof errors — zero on a long doc means it was never spell-checked
                proof_errors = raw.count(b"w:proofErr")

                # Tracked changes — presence proves human editing, absence is neutral alone
                has_tracked = b"w:ins " in raw or b"w:del " in raw

                # Byte-level AI phrase scan (fast — no parsing needed)
                lower_raw = raw.lower()
                matched_phrases = [p.decode() for p in AI_PHRASES if p in lower_raw]
                if len(matched_phrases) >= AI_PHRASE_THRESHOLD:
                    signals.append(
                        f"{len(matched_phrases)} AI boilerplate phrases detected "
                        f"(e.g. \"{matched_phrases[0]}\", \"{matched_phrases[1]}\")"
                    )

                # Parse paragraph structure
                body = ET.fromstring(raw).find(f"{{{W}}}body")
                if body is not None:
                    substantive = []  # (run_count, char_count) for paragraphs >50 chars
                    for p in body.iter(f"{{{W}}}p"):
                        runs = p.findall(f".//{{{W}}}r")
                        text = "".join(r.findtext(f"{{{W}}}t") or "" for r in runs).strip()
                        if len(text) > 50:
                            substantive.append((len(runs), len(text)))

                    if substantive:
                        # Single-run paragraphs: AI generators write each paragraph as one <w:r>
                        single_run = sum(1 for rc, _ in substantive if rc == 1)
                        pct = single_run / len(substantive)
                        if pct >= 0.75 and len(substantive) >= 5:
                            signals.append(
                                f"{pct:.0%} of substantive paragraphs are single-run "
                                f"({single_run}/{len(substantive)}) — hallmark of programmatic generation"
                            )
                            strong += 1
                        elif pct >= 0.55 and len(substantive) >= 8:
                            signals.append(
                                f"{pct:.0%} of substantive paragraphs are single-run "
                                f"({single_run}/{len(substantive)})"
                            )

                        # Zero spell-check markers relative to document size
                        approx_words = sum(ch for _, ch in substantive) // 5
                        if proof_errors == 0 and approx_words > 400 and not has_tracked:
                            signals.append(
                                f"zero spellcheck markers on ~{approx_words}-word doc "
                                "(human-typed docs accumulate these)"
                            )

    except zipfile.BadZipFile:
        signals.append("not a valid zip/docx — skipped")
    except Exception as e:
        signals.append(f"error during scan: {e}")

    result.signals = signals

    # Confidence scoring — error-only results are not AI flags
    error_only = all("error" in s or "skipped" in s for s in signals) if signals else False
    if not error_only:
        medium = len(signals) - strong
        score = strong * 2 + medium
        if score >= 4:
            result.flagged, result.confidence = True, "HIGH"
        elif score >= 2:
            result.flagged, result.confidence = True, "MEDIUM"
        elif score >= 1:
            result.flagged, result.confidence = True, "LOW"

    return result


def screen_directory(dirpath: str) -> list[ScreenResult]:
    results = []
    for fname in sorted(os.listdir(dirpath)):
        if fname.lower().endswith(".docx"):
            results.append(screen_docx(os.path.join(dirpath, fname)))
    return results


def main():
    parser = argparse.ArgumentParser(description="Fast AI-generation pre-screen for .docx files")
    parser.add_argument("--local-dir", required=True, help="Directory containing .docx files")
    parser.add_argument("--json", metavar="PATH", help="Write results as JSON to this path")
    parser.add_argument("--flagged-only", action="store_true", help="Only print flagged files")
    args = parser.parse_args()

    results = screen_directory(args.local_dir)
    flagged = [r for r in results if r.flagged]
    high    = [r for r in flagged if r.confidence == "HIGH"]
    medium  = [r for r in flagged if r.confidence == "MEDIUM"]

    print(f"\n{'='*64}")
    print(f"  AI Generation Pre-Screen — {len(results)} files scanned")
    print(f"  Flagged: {len(flagged)}  (HIGH: {len(high)}  MEDIUM: {len(medium)})")
    print(f"{'='*64}\n")

    for r in results:
        if args.flagged_only and not r.flagged:
            continue
        tag = f"[{r.confidence}]" if r.flagged else "[OK] "
        print(f"  {tag:8} {r.file_name}")
        for sig in r.signals:
            print(f"           • {sig}")
        if r.signals:
            print()

    if not args.flagged_only:
        clean = len(results) - len(flagged)
        print(f"  {clean} file(s) showed no AI signals\n")

    if args.json:
        out = [
            {"file_name": r.file_name, "flagged": r.flagged,
             "confidence": r.confidence, "signals": r.signals}
            for r in results
        ]
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"  JSON written to {args.json}\n")

    return len(flagged)


if __name__ == "__main__":
    main()
