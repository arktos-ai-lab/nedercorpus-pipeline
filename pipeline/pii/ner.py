"""
Named-entity-based PII detection using spaCy (optional).

Detects PERSON, LOC, and GPE entities that may constitute personal data
under GDPR Art. 4(1).

This module is optional: if spaCy or a Dutch model is not installed,
it degrades gracefully to a no-op and logs a warning.

Recommended model: nl_core_news_lg (best accuracy)
Acceptable:        nl_core_news_sm (faster, less accurate)

Install:
    pip install spacy
    python -m spacy download nl_core_news_lg
"""
from __future__ import annotations

import logging
from typing import Iterator

logger = logging.getLogger(__name__)

# Label → replacement token
_ENTITY_LABELS: dict[str, str] = {
    "PERSON":   "[NAAM]",
    "PER":      "[NAAM]",       # spaCy alternate label
    "ORG":      "[ORGANISATIE]",
    "LOC":      "[LOCATIE]",
    "GPE":      "[LOCATIE]",
    "FAC":      "[LOCATIE]",    # facility
}

_nlp = None
_nlp_attempted = False
_model_name: str | None = None

# Chunk size for NLP processing; overlap prevents missing entities at boundaries
_CHUNK_SIZE = 100_000
_OVERLAP = 2_000


def _load_model() -> object | None:
    global _nlp, _nlp_attempted, _model_name
    if _nlp_attempted:
        return _nlp
    _nlp_attempted = True
    try:
        import spacy  # noqa: F401
    except ImportError:
        logger.warning(
            "spaCy not installed — NER-based PII detection disabled. "
            "Install: pip install spacy && python -m spacy download nl_core_news_lg"
        )
        return None
    except Exception as exc:
        logger.warning(
            "spaCy failed to import (may be incompatible with Python %s): %s. "
            "NER disabled. Consider Python 3.11/3.12 for full spaCy support.",
            ".".join(str(v) for v in __import__("sys").version_info[:3]),
            exc,
        )
        return None

    for model in ("nl_core_news_lg", "nl_core_news_md", "nl_core_news_sm"):
        try:
            import spacy
            _nlp = spacy.load(model, disable=["parser", "lemmatizer"])
            _model_name = f"{model} v{_nlp.meta.get('version', 'unknown')}"
            logger.info("Loaded spaCy model: %s", _model_name)
            return _nlp
        except OSError:
            continue
        except Exception as exc:
            logger.warning("Failed to load spaCy model %s: %s", model, exc)
            continue

    logger.warning(
        "No Dutch spaCy model found. "
        "Install: python -m spacy download nl_core_news_lg"
    )
    return None


def is_available() -> bool:
    """Return True if a spaCy Dutch NER model is loaded and functional."""
    return _load_model() is not None


def get_model_name() -> str | None:
    """Return the loaded model name + version, or None if unavailable."""
    _load_model()
    return _model_name


def find_entities(text: str) -> list[tuple[str, int, int, str]]:
    """
    Find named entities in text that constitute PII.

    Returns list of (spacy_label, start, end, entity_text).
    Returns empty list if spaCy is unavailable.

    Chunks text with a 2k-char overlap to avoid missing entities that
    straddle chunk boundaries; results are deduplicated by position.
    """
    nlp = _load_model()
    if nlp is None:
        return []

    seen_spans: set[tuple[int, int]] = set()
    results: list[tuple[str, int, int, str]] = []
    step = _CHUNK_SIZE - _OVERLAP
    offset = 0

    while offset < len(text):
        chunk = text[offset: offset + _CHUNK_SIZE]
        try:
            doc = nlp(chunk)
        except Exception as exc:
            logger.warning("NER failed on chunk at offset %d: %s", offset, exc)
            offset += step
            continue

        for ent in doc.ents:
            label_key = _ENTITY_LABELS.get(ent.label_)
            if label_key is None:
                continue
            abs_start = offset + ent.start_char
            abs_end = offset + ent.end_char
            span_key = (abs_start, abs_end)
            if span_key not in seen_spans:
                seen_spans.add(span_key)
                results.append((ent.label_, abs_start, abs_end, ent.text))

        offset += step

    return results


def label_for(spacy_label: str) -> str | None:
    """Map a spaCy entity label to its replacement token, or None if not PII."""
    return _ENTITY_LABELS.get(spacy_label)
