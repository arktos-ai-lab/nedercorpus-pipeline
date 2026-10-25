"""
Extract plain text from PDF files.

Used by Belgian and Surinamese acquirers that save PDFs.
Uses pdfminer.six (pure Python, no system deps).

Falls back gracefully if pdfminer is not installed: returns None.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_PDFMINER_AVAILABLE = False
try:
    from pdfminer.high_level import extract_text as _pdf_extract
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    _PDFMINER_AVAILABLE = True
except ImportError:
    pass


def extract_text(pdf_path: Path) -> str | None:
    """
    Extract plain text from a PDF file.

    Returns None if pdfminer is not installed, the file is empty,
    password-protected, or too small to be useful.
    """
    if not _PDFMINER_AVAILABLE:
        logger.warning(
            "pdfminer.six not installed — PDF extraction skipped. "
            "Install with: pip install pdfminer.six"
        )
        return None

    if not pdf_path.exists() or pdf_path.stat().st_size < 100:
        return None

    try:
        text = _pdf_extract(str(pdf_path))
    except Exception as exc:
        logger.warning("PDF extraction failed %s: %s", pdf_path.name, exc)
        return None

    if not text:
        return None

    # Post-process: remove form-feed chars, normalize whitespace
    text = text.replace("\x0c", "\n\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text if len(text) >= 100 else None


def extract_dir(pdf_dir: Path) -> Iterator[tuple[Path, str]]:
    """Yield (pdf_path, text) for each parseable PDF in pdf_dir (recursive)."""
    for pdf_file in sorted(pdf_dir.rglob("*.pdf")):
        text = extract_text(pdf_file)
        if text:
            yield pdf_file, text
