#!/usr/bin/env python3
"""Extract PDF stamp annotations into machine-readable JSON.

Usage:
  python extract_stamps.py input.pdf
  python extract_stamps.py input.pdf --pretty
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Any


def _pdf_date_to_iso(date_str: str) -> str | None:
    """Convert PDF date format D:YYYYMMDDHHmmSSOHH'mm' to ISO-like string."""
    m = re.match(
        r"^D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})([+\-Z])?(\d{2})?'?(\d{2})?'?$",
        date_str,
    )
    if not m:
        return None

    y, mo, d, hh, mm, ss, sign, tzh, tzm = m.groups()
    base = f"{y}-{mo}-{d}T{hh}:{mm}:{ss}"

    if sign == "Z":
        return base + "Z"
    if sign in {"+", "-"} and tzh and tzm:
        return f"{base}{sign}{tzh}:{tzm}"
    return base


def _try_extract_with_pypdf(pdf_path: Path) -> dict[str, Any] | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return None

    reader = PdfReader(str(pdf_path))
    result: dict[str, Any] = {"file": str(pdf_path), "stamp_count": 0, "stamps": []}

    for page_index, page in enumerate(reader.pages, start=1):
        annots = page.get("/Annots") or []
        for annot_ref in annots:
            annot = annot_ref.get_object()
            subtype = annot.get("/Subtype")
            if str(subtype) != "/Stamp":
                continue

            stamp: dict[str, Any] = {
                "page": page_index,
                "subtype": "Stamp",
                "intent": str(annot.get("/IT")).lstrip("/") if annot.get("/IT") else None,
                "name": str(annot.get("/Name")).lstrip("/") if annot.get("/Name") else None,
                "subject": annot.get("/Subj"),
                "author": annot.get("/T"),
                "annotation_id": annot.get("/NM"),
                "rect": list(annot.get("/Rect")) if annot.get("/Rect") else None,
                "created_pdf_date": annot.get("/CreationDate"),
                "modified_pdf_date": annot.get("/M"),
            }
            stamp["created_iso"] = _pdf_date_to_iso(stamp["created_pdf_date"]) if stamp["created_pdf_date"] else None
            stamp["modified_iso"] = _pdf_date_to_iso(stamp["modified_pdf_date"]) if stamp["modified_pdf_date"] else None

            result["stamps"].append(stamp)

    result["stamp_count"] = len(result["stamps"])
    return result


def _fallback_extract_by_stream_scan(pdf_path: Path) -> dict[str, Any]:
    """Low-level fallback if pypdf isn't installed.

    Scans and decompresses Flate streams, then extracts stamp dictionaries heuristically.
    """
    raw = pdf_path.read_bytes()
    streams: list[bytes] = []

    for match in re.finditer(rb"stream\r?\n", raw):
        s = match.end()
        e = raw.find(b"endstream", s)
        if e == -1:
            continue
        compressed = raw[s:e].rstrip(b"\r\n")
        try:
            decompressed = zlib.decompress(compressed)
        except Exception:
            continue
        streams.append(decompressed)

    text = b"\n".join(streams).decode("latin-1", errors="ignore")

    # Find windows around each /Subtype /Stamp occurrence.
    blocks: list[str] = []
    for m in re.finditer(r"/Subtype\s*/Stamp", text):
        start = max(0, m.start() - 1500)
        end = min(len(text), m.end() + 1500)
        blocks.append(text[start:end])

    stamps: list[dict[str, Any]] = []
    for block in blocks:
        def pick(pattern: str, last: bool = False) -> str | None:
            if last:
                ms = re.findall(pattern, block, flags=re.DOTALL)
                return ms[-1] if ms else None
            m = re.search(pattern, block, flags=re.DOTALL)
            return m.group(1) if m else None

        rect_raw = pick(r"/Rect\s*\[([^\]]+)\]")
        rect = None
        if rect_raw:
            try:
                rect = [float(x) for x in rect_raw.split()]
            except Exception:
                rect = rect_raw

        created = pick(r"/CreationDate\s*\((.*?)\)", last=True)
        modified = pick(r"/M\s*\((.*?)\)", last=True)

        stamp = {
            "page": None,
            "subtype": "Stamp",
            "intent": pick(r"/IT\s*/([^\s/<>\[\]()]+)"),
            "name": pick(r"/Name\s*/([^\s/<>\[\]()]+)"),
            "subject": pick(r"/Subj\s*\((.*?)\)"),
            "author": pick(r"/T\s*\((.*?)\)"),
            "annotation_id": pick(r"/NM\s*\((.*?)\)"),
            "rect": rect,
            "created_pdf_date": created,
            "modified_pdf_date": modified,
            "created_iso": _pdf_date_to_iso(created) if created else None,
            "modified_iso": _pdf_date_to_iso(modified) if modified else None,
        }
        stamps.append(stamp)

    return {"file": str(pdf_path), "stamp_count": len(stamps), "stamps": stamps}


def extract_stamps(pdf_path: Path) -> dict[str, Any]:
    with_pypdf = _try_extract_with_pypdf(pdf_path)
    if with_pypdf is not None:
        return with_pypdf
    return _fallback_extract_by_stream_scan(pdf_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PDF stamp annotations as JSON.")
    parser.add_argument("pdf", type=Path, help="Path to input PDF")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(json.dumps({"error": f"File not found: {args.pdf}"}))
        return 1

    result = extract_stamps(args.pdf)
    if args.pretty:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
