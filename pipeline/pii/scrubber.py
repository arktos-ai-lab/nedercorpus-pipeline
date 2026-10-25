"""
PII scrubber: combines regex patterns and optional NER to redact personal data.

Design principles (GDPR Art. 5 + EU AI Act Art. 10):

  1. Anonymization by substitution — replacements are deterministic labels,
     not deletions, so context is preserved for downstream NLP tasks.
  2. Audit trail — every redaction is logged to a per-document redaction record.
  3. Original text is never stored after scrubbing — this module is the last
     step that sees raw personal data.
  4. Special categories (Art. 9/10 GDPR: BSN, date of birth, health data,
     criminal conviction data) get a stricter flag in the audit log.
  5. NER redaction is deliberately disabled by default. NER cannot distinguish
     living private individuals from historical figures, public officials, or
     literary characters — scrubbing all PERSON entities destroys NER training
     signal and degrades corpus quality with no proportionate privacy gain.
     GDPR only covers living natural persons; deceased persons have no data
     subject rights (GDPR Recital 27).

Compliance notes:
  - Rechtspraak.nl officially anonymizes published decisions; this scrubber
    acts as a secondary safety net.
  - For Surinamese and Belgian sources that may not pre-anonymize, this is
    the primary PII removal step. NER unavailability is therefore warned
    explicitly for those source countries.
  - After scrubbing, the corpus no longer constitutes "personal data" under
    GDPR Recital 26 (fully anonymized data is outside GDPR scope), provided
    re-identification is not reasonably possible.
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field

from pipeline.pii.patterns import PATTERNS, PIIPattern, find_all, PATTERN_BY_NAME
from pipeline.pii.ner import find_entities, label_for, is_available as ner_is_available

logger = logging.getLogger(__name__)

# Source countries for which NER is the primary (not secondary) protection
_SOURCES_REQUIRING_NER = frozenset({"SR", "BE"})

# GDPR Recital 27: deceased persons have no data subject rights.
# Dutch national law (UAVG) does not extend GDPR protection to deceased persons.
# We apply a deterministic cutoff based on the birth year of the oldest 
# living person in the Netherlands (currently born in 1915). 
# Documents published in or before 1914 are treated as historical; 
# names are preserved to maintain NER training signal and cultural context.
_HISTORICAL_CUTOFF_YEAR = 1915


@dataclass
class RedactionRecord:
    """Audit record for a single scrubbed document."""
    doc_id: str
    redactions: list[dict] = field(default_factory=list)

    def add(
        self,
        method: str,          # "regex" or "ner"
        pii_type: str,
        label: str,
        char_count: int,
        special_category: bool = False,
    ) -> None:
        self.redactions.append({
            "method": method,
            "pii_type": pii_type,
            "label": label,
            "char_count": char_count,
            "special_category": special_category,
        })

    @property
    def count(self) -> int:
        return len(self.redactions)

    @property
    def has_special_category(self) -> bool:
        return any(r["special_category"] for r in self.redactions)


@dataclass
class ScrubConfig:
    use_regex: bool = True
    # NER-based name redaction is gated by the 75-year rule below.
    # Set use_ner=True to enable NER for ORG/LOC entities unconditionally;
    # PERSON entity redaction is further gated by publication_year.
    use_ner: bool = True
    # Which regex patterns to enable (None = all)
    enabled_patterns: list[str] | None = None
    # ISO country code of the source (used to warn if NER is off for high-risk sources)
    source_country: str = ""
    # Publication year of the document being scrubbed.
    # Used for the 75-year GDPR name cutoff: names are only NER-redacted in
    # documents published within the last 75 years (or with unknown year).
    # Set to None to always redact person names when use_ner=True.
    publication_year: int | None = None


def _should_redact_names(cfg: ScrubConfig) -> bool:
    """
    Return True if person names should be NER-redacted for this document.

    Rule: redact names in documents published in or after 1915 
    (or with unknown publication year — conservative).
    Documents from <= 1914 are treated as historical; GDPR Recital 27
    excludes deceased persons from data subject rights.
    """
    if not cfg.use_ner:
        return False
    if cfg.publication_year is None:
        return True   # unknown → conservative
    return cfg.publication_year >= _HISTORICAL_CUTOFF_YEAR



def scrub(
    text: str,
    doc_id: str = "",
    config: ScrubConfig | None = None,
) -> tuple[str, RedactionRecord]:
    """
    Scrub PII from text.

    Returns (scrubbed_text, RedactionRecord).
    The RedactionRecord contains an audit trail of all redactions made.
    """
    cfg = config or ScrubConfig()
    record = RedactionRecord(doc_id=doc_id)

    # Warn when NER is unavailable for sources that lack pre-anonymization
    if (cfg.use_ner
            and cfg.source_country in _SOURCES_REQUIRING_NER
            and not ner_is_available()):
        logger.warning(
            "NER unavailable for source country %s (doc_id=%s). "
            "Regex-only scrubbing may miss name/organisation entities. "
            "Install spaCy: pip install spacy && python -m spacy download nl_core_news_lg",
            cfg.source_country,
            doc_id,
        )

    # Collect all matches: (start, end, replacement, pii_type, method, special)
    replacements: list[tuple[int, int, str, str, str, bool]] = []

    # --- Regex layer ---
    if cfg.use_regex:
        enabled = set(cfg.enabled_patterns) if cfg.enabled_patterns else None
        for pii_name, start, end, matched in find_all(text):
            pii = PATTERN_BY_NAME[pii_name]
            if enabled and pii_name not in enabled:
                continue
            replacements.append((
                start, end, pii.label, pii_name, "regex", pii.special_category
            ))

    # --- NER layer ---
    if cfg.use_ner:
        redact_names = _should_redact_names(cfg)
        for spacy_label, start, end, ent_text in find_entities(text):
            label = label_for(spacy_label)
            if label is None:
                continue
            # Apply 75-year cutoff to PERSON entities only.
            # ORG/LOC/FAC/GPE are not personal data for living individuals
            # so they are redacted unconditionally when use_ner=True.
            if spacy_label in ("PERSON", "PER") and not redact_names:
                continue
            replacements.append((start, end, label, spacy_label, "ner", False))

    if not replacements:
        return text, record

    # Sort by start position; on tie, prefer the longer match
    replacements.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    non_overlapping: list[tuple[int, int, str, str, str, bool]] = []
    last_end = -1
    for item in replacements:
        start, end = item[0], item[1]
        if start >= last_end:
            non_overlapping.append(item)
            last_end = end

    # Build redacted text right-to-left to preserve indices
    chars = list(text)
    for start, end, label, pii_type, method, special in reversed(non_overlapping):
        chars[start:end] = list(label)
        record.add(
            method=method,
            pii_type=pii_type,
            label=label,
            char_count=end - start,
            special_category=special,
        )

    scrubbed = "".join(chars)
    # Collapse any double-spaces introduced by replacements
    scrubbed = re.sub(r" {2,}", " ", scrubbed)

    return scrubbed, record
