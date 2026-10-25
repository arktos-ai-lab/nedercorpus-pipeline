"""
Full document cleaning pipeline.

Takes raw text (HTML or plain), returns cleaned plain text:
  1. HTML stripping
  2. Encoding normalization (UTF-8 + NFC)
  3. Whitespace normalization
  4. Minimum quality filters
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pipeline.clean.html import strip_html
from pipeline.clean.encoding import normalize_unicode


@dataclass
class CleanConfig:
    strip_html: bool = True
    min_chars: int = 100          # discard documents shorter than this
    max_chars: int = 10_000_000   # discard absurdly large documents
    min_alpha_ratio: float = 0.5  # minimum fraction of alphabetic characters
    max_digit_ratio: float = 0.4  # maximum fraction of digit characters


def _is_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|body|div|p|span)\b", text, re.IGNORECASE))


def _alpha_ratio(text: str) -> float:
    if not text:
        return 0.0
    alpha = sum(1 for c in text if c.isalpha())
    return alpha / len(text)


def _digit_ratio(text: str) -> float:
    if not text:
        return 0.0
    digits = sum(1 for c in text if c.isdigit())
    return digits / len(text)


def clean(text: str, config: CleanConfig | None = None) -> str | None:
    """
    Clean a single document.

    Returns cleaned text, or None if the document fails quality checks.
    """
    cfg = config or CleanConfig()

    # 1. Strip HTML if detected or always-strip is set
    if cfg.strip_html and _is_html(text):
        text = strip_html(text)

    # 2. Unicode normalization
    text = normalize_unicode(text)

    # 3. Collapse excessive whitespace (keep single newlines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # 4. Quality filters
    if len(text) < cfg.min_chars:
        return None
    if len(text) > cfg.max_chars:
        return None
    if _alpha_ratio(text) < cfg.min_alpha_ratio:
        return None
    if _digit_ratio(text) > cfg.max_digit_ratio:
        return None

    return text


def clean_batch(texts: list[str], config: CleanConfig | None = None) -> list[str | None]:
    """Clean a batch of texts. Returns list of same length; None entries failed QC."""
    return [clean(t, config) for t in texts]
