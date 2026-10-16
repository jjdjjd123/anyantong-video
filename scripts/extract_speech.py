#!/usr/bin/env python3
"""Extract briefing speech text into a JSON map of {name: [text]}.

Usage:
    python extract_speech.py <input.xlsx|input.docx> <output.json> [--cols 2,3]

.xlsx  Reads the active sheet. Column 2 (B) holds the name, column 3 (C) holds
       the speech body. Override with --cols for a different layout.
.docx  Walks the body paragraphs. A chapter heading is a CJK numeral followed by
       the ideographic comma (e.g. U+4E00 U+3001). A speech entry is "N. title"
       and its body is the following paragraphs up to the next entry or chapter.

The resulting JSON is consumed by make_briefing_video.py through its
speech_json plus key fields.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# CJK numerals and the ideographic comma, written as escapes so this file stays
# pure ASCII end to end.
_CJK_NUM = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341"
_IDEO_COMMA = "\u3001"

_CHAPTER_RE = re.compile(rf"^[{_CJK_NUM}]{{1,3}}{re.escape(_IDEO_COMMA)}")
# "12. title" or "12.5 sub" style numbering, ASCII dot or fullwidth dot.
_ENTRY_RE = re.compile(r"^(\d{1,2})[.\uff0e]\s*(.+)$")
_SUB_RE = re.compile(r"^\d{1,2}[.\uff0e]\d")
# Sentence enders used to reject body paragraphs that merely start with "N. ".
_SENTENCE_END = "\u3002\uff01\uff1f\uff1b"


def _entry_title(text):
    """Return the title of a numbering-led paragraph, or None for body text."""
    m = _ENTRY_RE.match(text)
    if not m or _SUB_RE.match(text):
        return None
    title = m.group(2).strip()
    if any(ch in title for ch in _SENTENCE_END):
        return None
    return title


def from_xlsx(path, cols):
    import openpyxl

    name_col, text_col = cols
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    data = {}
    for r in range(2, ws.max_row + 1):
        name = ws.cell(row=r, column=name_col).value
        text = ws.cell(row=r, column=text_col).value
        if name and text:
            data[str(name).strip()] = [str(text).strip()]
    return data


def from_docx(path):
    from docx import Document

    doc = Document(path)
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    data = {}
    current = None
    body = []

    def flush():
        if current and body:
            data[current] = ["".join(body)]

    for text in paras:
        if _CHAPTER_RE.match(text):
            flush()
            current, body = None, []
            continue
        title = _entry_title(text)
        if title:
            flush()
            current = title
            body = []
            continue
        if current is None:
            continue
        if _SUB_RE.match(text):
            # Sub-section numbering belongs to the body, keep its text only.
            continue
        body.append(text)

    flush()
    return data


def main():
    ap = argparse.ArgumentParser(description="Extract briefing speeches to JSON")
    ap.add_argument("input", help="path to .xlsx or .docx")
    ap.add_argument("output", help="path to the output JSON")
    ap.add_argument("--cols", default="2,3", help="xlsx name,text columns (1-based)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"input not found: {src}")

    suffix = src.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        cols = tuple(int(c) for c in args.cols.split(","))
        data = from_xlsx(src, cols)
    elif suffix in (".docx", ".doc"):
        data = from_docx(src)
    else:
        sys.exit(f"unsupported input type: {suffix}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"entries: {len(data)}")
    for name, body in data.items():
        print(f"  {name}: {len(body[0])} chars")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
