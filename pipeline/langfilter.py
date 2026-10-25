"""
Language identification filter.

Keeps only Dutch (nl) documents, with special handling for Surinamese Dutch
(also 'nl' according to langdetect but flagged via country='SR' metadata).

Uses langdetect (pip install langdetect). Falls back gracefully if not installed:
all documents pass through unfiltered with a warning.

Why we need this even for 'Dutch' sources:
  - EUR-Lex multilingual pages may contain French/English fragments
  - Belgian Staatsblad publishes bilingual NL/FR issues
  - Wikipedia articles sometimes start in the wrong language
  - Scraped web content may have language-switched paragraphs
  - Parliamentary proceedings may contain quoted foreign text

Strategy:
  - Classify the full document (fast path)
  - If uncertain, classify the first 2000 chars
  - Accept: nl (Dutch), af (Afrikaans — close enough for Dutch corpus)
  - Reject: anything else

Surinamese Dutch is classified as 'nl' by langdetect, so no special
handling is needed for SR-origin documents.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pipeline.document import Document

logger = logging.getLogger(__name__)

_LANGDETECT_AVAILABLE = False
_DETECT_FN = None

try:
    from langdetect import detect, LangDetectException
    _LANGDETECT_AVAILABLE = True
    _DETECT_FN = detect
except ImportError:
    pass

# Languages we accept in the corpus
ACCEPTED_LANGS = {"nl", "af"}   # Dutch + Afrikaans (very similar)


@dataclass
class LangFilterConfig:
    accepted_langs: set[str] | None = None   # None = use ACCEPTED_LANGS
    min_chars_for_detection: int = 50        # skip detection on very short texts
    pass_on_error: bool = True               # if detection fails, keep the doc


def detect_lang(text: str) -> str | None:
    """Detect language of text. Returns ISO 639-1 code or None on failure."""
    if not _LANGDETECT_AVAILABLE or not _DETECT_FN:
        return None
    sample = text[:5000]  # langdetect works well on first 5k chars
    try:
        return _DETECT_FN(sample)
    except Exception:
        return None


def is_dutch(text: str, config: LangFilterConfig | None = None) -> bool:
    """
    Return True if text is in an accepted language (Dutch by default).

    If langdetect is not available, always returns True.
    """
    cfg = config or LangFilterConfig()
    accepted = cfg.accepted_langs or ACCEPTED_LANGS

    if not _LANGDETECT_AVAILABLE:
        return True  # pass-through mode

    if len(text) < cfg.min_chars_for_detection:
        return cfg.pass_on_error

    lang = detect_lang(text)
    if lang is None:
        return cfg.pass_on_error

    return lang in accepted


def filter_document(doc: Document, config: LangFilterConfig | None = None) -> bool:
    """Return True if the document should be kept."""
    # Trust source metadata for countries/variants where langdetect is unreliable:
    # SR (Surinamese Dutch), BE (Belgian Dutch), ZA (Afrikaans) — langdetect
    # often misclassifies short government texts and Afrikaans as non-Dutch.
    if doc.meta.country in ("SR", "BE", "ZA"):
        return True
    # Also trust explicit language_variant metadata for accepted variants
    lv = (doc.meta.language_variant or "").upper()
    if lv in ("NL", "NL-BE", "NL-AW", "NL-CW", "FY", "AF", "LI", "VLS", "ZEA",
              "NDS-NL", "FRR", "STQ", "PAP", "SRN"):
        return True
    return is_dutch(doc.text, config)


def filter_batch(
    documents: list[Document],
    config: LangFilterConfig | None = None,
) -> tuple[list[Document], int]:
    """
    Filter a batch of documents.

    Returns (kept_documents, dropped_count).
    """
    kept, dropped = [], 0
    for doc in documents:
        if filter_document(doc, config):
            kept.append(doc)
        else:
            dropped += 1
    return kept, dropped
