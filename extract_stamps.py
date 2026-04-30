#!/usr/bin/env python3
"""Extract PDF stamp annotations into machine-readable JSON."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import zlib
from pathlib import Path
from typing import Any


def _pdf_date_to_iso(date_str: str) -> str | None:
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


def _pypdf_available() -> bool:
    return importlib.util.find_spec("pypdf") is not None


def _extract_with_pypdf(pdf_path: Path) -> dict[str, Any]:
    PdfReader = importlib.import_module("pypdf").PdfReader
    reader = PdfReader(str(pdf_path))
    stamps: list[dict[str, Any]] = []

    for page_index, page in enumerate(reader.pages, start=1):
        annots = page.get("/Annots") or []
        for annot_ref in annots:
            annot = annot_ref.get_object()
            if str(annot.get("/Subtype")) != "/Stamp":
                continue
            created = annot.get("/CreationDate")
            modified = annot.get("/M")
            stamps.append(
                {
                    "page": page_index,
                    "subtype": "Stamp",
                    "intent": str(annot.get("/IT")).lstrip("/") if annot.get("/IT") else None,
                    "name": str(annot.get("/Name")).lstrip("/") if annot.get("/Name") else None,
                    "subject": annot.get("/Subj"),
                    "author": annot.get("/T"),
                    "annotation_id": annot.get("/NM"),
                    "rect": list(annot.get("/Rect")) if annot.get("/Rect") else None,
                    "created_pdf_date": created,
                    "modified_pdf_date": modified,
                    "created_iso": _pdf_date_to_iso(created) if created else None,
                    "modified_iso": _pdf_date_to_iso(modified) if modified else None,
                }
            )

    return {"file": str(pdf_path), "extraction_method": "pypdf", "stamp_count": len(stamps), "stamps": stamps}


def _extract_with_stream_scan(pdf_path: Path) -> dict[str, Any]:
    raw = pdf_path.read_bytes()
    streams: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n", raw):
        start = match.end()
        end = raw.find(b"endstream", start)
        if end == -1:
            continue
        compressed = raw[start:end].rstrip(b"\r\n")
        try:
            streams.append(zlib.decompress(compressed))
        except Exception:
            continue

    text = b"\n".join(streams).decode("latin-1", errors="ignore")
    blocks: list[str] = []
    for m in re.finditer(r"/Subtype\s*/Stamp", text):
        blocks.append(text[max(0, m.start() - 2000) : min(len(text), m.end() + 2000)])

    def pick(block: str, pattern: str, last: bool = False) -> str | None:
        if last:
            matches = re.findall(pattern, block, flags=re.DOTALL)
            return matches[-1] if matches else None
        m = re.search(pattern, block, flags=re.DOTALL)
        return m.group(1) if m else None

    stamps: list[dict[str, Any]] = []
    for block in blocks:
        rect_raw = pick(block, r"/Rect\s*\[([^\]]+)\]")
        rect: list[float] | str | None = None
        if rect_raw:
            try:
                rect = [float(x) for x in rect_raw.split()]
            except ValueError:
                rect = rect_raw
        created = pick(block, r"/CreationDate\s*\((.*?)\)", last=True)
        modified = pick(block, r"/M\s*\((.*?)\)", last=True)
        stamps.append(
            {
                "page": None,
                "subtype": "Stamp",
                "intent": pick(block, r"/IT\s*/([^\s/<>'\[\]()]+)"),
                "name": pick(block, r"/Name\s*/([^\s/<>'\[\]()]+)"),
                "subject": pick(block, r"/Subj\s*\((.*?)\)"),
                "author": pick(block, r"/T\s*\((.*?)\)"),
                "annotation_id": pick(block, r"/NM\s*\((.*?)\)"),
                "rect": rect,
                "created_pdf_date": created,
                "modified_pdf_date": modified,
                "created_iso": _pdf_date_to_iso(created) if created else None,
                "modified_iso": _pdf_date_to_iso(modified) if modified else None,
            }
        )

    return {"file": str(pdf_path), "extraction_method": "stream_scan", "stamp_count": len(stamps), "stamps": stamps}


def extract_stamps(pdf_path: Path) -> dict[str, Any]:
    if _pypdf_available():
        return _extract_with_pypdf(pdf_path)
    return _extract_with_stream_scan(pdf_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PDF stamp annotations as JSON")
    parser.add_argument("pdf", type=Path, help="Path to input PDF")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(json.dumps({"error": f"File not found: {args.pdf}"}))
        return 1

    result = extract_stamps(args.pdf)
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
