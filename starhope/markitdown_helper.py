"""Local, bounded MarkItDown recovery for rejected, provably plain PDF pages.

Never sends a document to a cloud converter. Mathematical/illustrated pages
stay on the existing OCR path; this is an alternative extractor, not a proof.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from starhope.paths import PROJECT_ROOT
from starhope.processing_timeouts import DOCUMENT_TEXT_EXTRACTION_TIMEOUT_SECONDS

MAX_PAGES = 10
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_RESULT_BYTES = 80_000


def _convert_pages(directory: Path) -> None:
    # Use the format-specific converter: no URI dispatch, plugins, Magika model,
    # Azure integration, image captions, or implicit model clients are involved.
    from markitdown import StreamInfo
    from markitdown.converters import PdfConverter

    converter = PdfConverter()
    for path in sorted(directory.glob("*.pdf"))[:MAX_PAGES]:
        try:
            with path.open("rb") as stream:
                result = converter.convert(stream, StreamInfo(extension=".pdf", mimetype="application/pdf"))
            text = result.markdown.strip()
            # pdfminer emits paragraph blanks and a final form feed. Only
            # normalize these layout separators; never join/rewrite text lines.
            text = re.sub(r"\n[ \t]*\n+", "\n", text)
            payload = json.dumps({"markdown": text}, ensure_ascii=False).encode("utf-8")
            if len(payload) <= MAX_RESULT_BYTES:
                path.with_suffix(".json").write_bytes(payload)
        except Exception:
            continue  # One bad page must not discard other local results.


def _run_converter(directory: Path, check_cancelled) -> str:
    process = subprocess.Popen(
        [sys.executable, "-m", "starhope.markitdown_helper", str(directory)],
        cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + DOCUMENT_TEXT_EXTRACTION_TIMEOUT_SECONDS
    try:
        while True:
            check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            try:
                return "completed" if process.wait(timeout=min(.25, remaining)) == 0 else "unavailable"
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def recover_pdf_text_pages(file_bytes: bytes, pages: list[dict], *, check_cancelled=lambda: None) -> None:
    """Update only fully proved alternatives, preserving original text/reasons.

    ponytail: at most ten pages per import, one 30-second child process; expand
    only after profiling real documents, rather than multiplying local work.
    """
    rejected = [row for row in pages if row.get("needs_ocr") and row.get("source") != "pdf-inspector-legacy"]
    if not rejected:
        return
    import pymupdf as fitz
    from starhope.pdf_source_scopes import _physical_assessment
    from starhope.pdf_inspector_helper import native_text_quality_reasons, _has_math_formula_loss

    with fitz.open(stream=file_bytes, filetype="pdf") as document, tempfile.TemporaryDirectory(
        prefix="starhope-markitdown-"
    ) as temporary:
        directory = Path(temporary)
        candidates, fonts, total_bytes = [], {}, 0
        for row in rejected[:MAX_PAGES]:
            check_cancelled()
            index = row.get("page_index")
            if type(index) is not int or not 0 <= index < len(document):
                continue
            page = document[index]
            # Reuse the original glyph/font/visibility/reading-order proof.
            # No formulas, figures, hidden text, columns or unknown fonts pass.
            physical = page.get_text("text").strip()
            proof = _physical_assessment(page, {"markdown": physical, "source": "pymupdf"}, fonts)
            if not proof.get("reliable") or len(physical) < 30 or native_text_quality_reasons(physical):
                continue
            with fitz.open() as single:
                single.insert_pdf(document, from_page=index, to_page=index)
                data = single.tobytes(garbage=4, deflate=True)
            if len(data) > MAX_PAGE_BYTES or total_bytes + len(data) > MAX_TOTAL_BYTES:
                continue
            total_bytes += len(data)
            (directory / f"{index}.pdf").write_bytes(data)
            candidates.append((row, physical))
        if not candidates:
            return
        check_cancelled()
        status = _run_converter(directory, check_cancelled)
        for row, physical in candidates:
            check_cancelled()
            row["markitdown"] = {"status": status, "adopted": False}
            path = directory / f"{row['page_index']}.json"
            try:
                if path.stat().st_size > MAX_RESULT_BYTES:
                    continue
                text = json.loads(path.read_text(encoding="utf-8"))["markdown"]
                if (not isinstance(text, str) or text != physical or native_text_quality_reasons(text)
                        or _has_math_formula_loss(text)):
                    row["markitdown"]["status"] = "content_mismatch"
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            row["original_markdown"] = row["markdown"]
            row["markitdown"].update(status="recovered", adopted=True,
                original_quality_reasons=list(row.get("quality_reasons", [])), characters=len(text))
            row.update(markdown=text, needs_ocr=False, quality_reasons=[], source="markitdown")


if __name__ == "__main__":
    _convert_pages(Path(sys.argv[1]))
