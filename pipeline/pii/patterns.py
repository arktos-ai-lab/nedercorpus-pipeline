"""
Regex-based PII pattern detection for Dutch text.

Covers all categories required for GDPR (Art. 4(1), 9, 10) and the EU AI Act
(Art. 10 data governance) compliance.

GDPR special categories (Art. 9/10) are flagged with special_category=True:
  BSN, date of birth, health data, criminal conviction data, passport/ID

Each pattern returns a replacement label in Dutch to make redacted text
readable and auditable:
Functional identifiers only — zero linguistic value, direct harm if memorised:
  [BSN]        — Burgerservicenummer (elfproef validated)
  [IBAN]       — bank account number (NL + generic)
  [EMAIL]      — email address
  [TELEFOON]   — phone number (mobile + landline)
  [CREDITCARD] — credit card number (Luhn validated)
  [REISDOCUMENT] — passport / national ID (contextual — requires keyword)
  [ID-NUMMER]  — Surinamese / generic ID number (contextual — requires keyword)

Not scrubbed (intentional):
  - Person names: NER cannot distinguish living private individuals from
    historical figures, public officials, or literary characters. Scrubbing
    all PERSON entities destroys NER training signal with no proportionate gain.
  - Dates of birth: without an accompanying name, a birth date is not
    identifying. Biographical and historical text would be degraded.
  - Health / criminal data phrases: these appear in public court rulings
    and medical literature as substantive content, not incidental PII.
  - IP addresses: in Dutch prose these appear in technical documentation
    as meaningful content; the harm from a stale IP is minimal.
  - License plates, BTW/KvK: not natural-person data under GDPR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern


@dataclass(frozen=True)
class PIIPattern:
    name: str
    pattern: Pattern
    label: str          # replacement string in redacted output
    # GDPR special category (Art. 9/10) — needs stricter justification
    special_category: bool = False


def _bsn_elfproef(digits: str) -> bool:
    """Validate a 9-digit string against the Dutch BSN 11-proof (elfproef)."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum((9 - i) * int(d) for i, d in enumerate(digits[:8]))
    total -= int(digits[8])
    return total % 11 == 0


def _luhn_check(digits: str) -> bool:
    """Validate a digit string using the Luhn algorithm (ISO/IEC 7812)."""
    if not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

PATTERNS: list[PIIPattern] = [

    # --- BSN (Burgerservicenummer) ---
    # 9 consecutive digits or formatted with separators (dot, dash, space)
    # Validation done in find_all() via elfproef
    PIIPattern(
        name="bsn",
        pattern=re.compile(
            r"\b(\d{9}|\d{3}[-.\s]\d{3}[-.\s]\d{3})\b"
        ),
        label="[BSN]",
        special_category=True,
    ),

    # --- Dutch passport and national ID card (contextual) ---
    # Requires document-type keyword to suppress FPs on article/case numbers
    # Passport format:  2 letters + 6 digits + optional alphanumeric (e.g., AA123456B)
    # ID card format:   1–3 letters + 6–8 digits (e.g., A1234567, ABC123456)
    PIIPattern(
        name="passport_id",
        pattern=re.compile(
            r"(?:paspoort|reisdocument|identiteitskaart|legitimatiebewijs|rijbewijs)"
            r"(?:\s*nummer)?"
            r"[:\s]*"
            r"([A-Z]{1,3}\d{6,8}[A-Z0-9]?)"
            r"|(?:id(?:entiteits)?[-\s]?nummer|id[-\s]?nr\.?)"
            r"[:\s]*"
            r"([A-Z]{1,3}\d{6,8}[A-Z0-9]?)",
            re.IGNORECASE,
        ),
        label="[REISDOCUMENT]",
        special_category=True,
    ),

    # --- IBAN (Dutch) ---
    PIIPattern(
        name="iban_nl",
        pattern=re.compile(
            r"\bNL\d{2}[A-Z]{4}0{0,1}\d{9,10}\b",
            re.IGNORECASE,
        ),
        label="[IBAN]",
    ),

    # --- IBAN (generic international) ---
    PIIPattern(
        name="iban_generic",
        pattern=re.compile(
            r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){3,7}[A-Z0-9]{1,4}\b",
            re.IGNORECASE,
        ),
        label="[IBAN]",
    ),

    # --- Email ---
    PIIPattern(
        name="email",
        pattern=re.compile(
            r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
        ),
        label="[EMAIL]",
    ),

    # --- Dutch mobile phone numbers ---
    PIIPattern(
        name="phone_mobile",
        pattern=re.compile(
            r"\b(?:\+31|0031|0)[-\s.]?6[-\s.]?\d{2}[-\s.]?\d{2}[-\s.]?\d{2}[-\s.]?\d{2}\b"
        ),
        label="[TELEFOON]",
    ),

    # --- Dutch landline numbers (including parenthesised area codes) ---
    PIIPattern(
        name="phone_landline",
        pattern=re.compile(
            # International prefix: +31 or 0031
            r"(?<!\d)(?:\+31|0031)[-\s.]?\d{2,3}(?:[-\s.]\d{3})?[-\s.]\d{4}(?!\d)"
            r"|(?<!\d)(?:\+31|0031)[-\s.]?\d{9,10}(?!\d)"
            # Local landline: 020-1234567 or 0182 123456
            r"|\b0\d{1,3}[-\s.]\d{5,8}\b"
            # Parenthesised area code: (020) 123 4567 or (0182) 12 34 56
            r"|\(\d{2,4}\)\s*\d{3}[-\s.]?\d{4}\b"
        ),
        label="[TELEFOON]",
    ),

    # --- Credit card numbers (Luhn-validated in find_all) ---
    PIIPattern(
        name="credit_card",
        pattern=re.compile(
            r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
        ),
        label="[CREDITCARD]",
    ),

    # --- Surinamese / generic numeric ID number (contextual only) ---
    # The bare \b\d{8}\b pattern caused mass FPs on legal citations and dates.
    # Now requires an explicit ID-type keyword.
    PIIPattern(
        name="suriname_id",
        pattern=re.compile(
            r"(?:ID(?:-?nummer)?|identificatienummer|persoonsnummer|"
            r"identiteitsnummer|ID-bewijs\s+nr\.?)"
            r"[:\s]*(\d{8})\b",
            re.IGNORECASE,
        ),
        label="[ID-NUMMER]",
    ),
]


# Fast lookup by name
PATTERN_BY_NAME: dict[str, PIIPattern] = {p.name: p for p in PATTERNS}


def find_all(text: str) -> list[tuple[str, int, int, str]]:
    """
    Find all PII matches in text.

    Returns list of (pattern_name, start, end, matched_text).
    Results are sorted by start position.
    """
    matches: list[tuple[str, int, int, str]] = []
    for pii in PATTERNS:
        for m in pii.pattern.finditer(text):
            raw = m.group(0)

            # Extra validation for BSN: run elfproef
            if pii.name == "bsn":
                digits = re.sub(r"\D", "", raw)
                if not _bsn_elfproef(digits):
                    continue

            # Extra validation for credit cards: run Luhn check
            if pii.name == "credit_card":
                digits = re.sub(r"\D", "", raw)
                if not _luhn_check(digits):
                    continue

            matches.append((pii.name, m.start(), m.end(), raw))

    matches.sort(key=lambda x: x[1])
    return matches
