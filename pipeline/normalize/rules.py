"""
Rule-based normalization for historical Dutch text (17th–19th century).

Rules cover the most systematic orthographic conventions of Early Modern Dutch
as found in DBNL literary texts and VOC/WIC administrative archives.
They are applied in order; the sequence matters for rules that share substrings.

Rules deliberately NOT included (too context-dependent for safe regex):
  - dt → d/t  (word-final; maakt vs. wordt requires morphological analysis)
  - v/f initial confusion (vreede vs. vrede; false-positive rate too high)
  - soo → zo (soo means both "zo" and "zo" but also appears in compounds)
"""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [

    # --- Character-level ---
    (re.compile(r"ſ"),       "s"),     # long-s (Unicode U+017F)
    (re.compile(r"æ"),       "ae"),    # ligature ae
    (re.compile(r"œ"),       "oe"),    # ligature oe

    # --- ck → k (final and medial, but not in acronyms/caps runs) ---
    # "volcken" → "volken", "marckt" → "markt", "volcke" → "volke"
    (re.compile(r"ck(?=[a-zäöüáéíóú\s,.:;!?\-]|$)", re.IGNORECASE), "k"),

    # --- gh → g  (word-initial AND medial) ---
    # "gheen" → "geen", "ghenomen" → "genomen", "seght" → "segt" (medial: ght → gt)
    (re.compile(r"\bgh", re.IGNORECASE), "g"),
    (re.compile(r"ght\b"),  "gt"),     # word-final ght: "seght" → "segt"

    # --- uy / uij / uy → ui ---
    # "huys" → "huis", "buyten" → "buiten", "uijt" → "uit"
    (re.compile(r"uij", re.IGNORECASE), "ui"),
    (re.compile(r"uy(?=[a-z])", re.IGNORECASE), "ui"),

    # --- ey / eij → ei ---
    # "zeyde" → "zei", "gezeyd" → "gezeid", "weyde" → "weide"
    (re.compile(r"eij", re.IGNORECASE), "ei"),
    (re.compile(r"ey(?=[a-z])", re.IGNORECASE), "ei"),

    # --- ae → aa (before any letter) ---
    # Handles the majority of cases: staet→staat, daer→daar, waer→waar.
    # Some short-a words (maecken→maakcken) over-expand; those are cleaned by
    # the LLM pass or lexicon post-processing.
    (re.compile(r"ae(?=[a-z])", re.IGNORECASE), "aa"),

    # --- -lyck / -lijck → -lijk ---
    # "heerlyck" → "heerlijk", "welckelycke" → "welkelijke"
    (re.compile(r"lijck", re.IGNORECASE), "lijk"),
    (re.compile(r"lyck",  re.IGNORECASE), "lijk"),

    # --- -igh / -igh suffix → -ig ---
    # "heerlyckigh" → "heerlijkig" (combined with above)
    # "waarachtigh" → "waarachtig"
    (re.compile(r"igh\b", re.IGNORECASE), "ig"),

    # --- -tlijck / -telijk ---
    (re.compile(r"tlijck\b", re.IGNORECASE), "telijk"),

    # --- -heijt / -heit → -heid ---
    (re.compile(r"h[ei]ijt\b", re.IGNORECASE), "heid"),
    (re.compile(r"heit\b",     re.IGNORECASE), "heid"),

    # --- Common abbreviations in VOC/WIC documents ---
    # These are expanded before other rules run so the expansions
    # can benefit from further normalization.
    (re.compile(r"\bd[:\.]o\b",      re.IGNORECASE), "dito"),
    (re.compile(r"\bcomp[:\.]e\b",   re.IGNORECASE), "compagnie"),
    (re.compile(r"\bgen[:\.]t\b",    re.IGNORECASE), "generaal"),
    (re.compile(r"\bpr[:\.]?\b(?=[A-Z])", re.IGNORECASE), "predikant "),
    (re.compile(r"\bvt[:\.]?\b",     re.IGNORECASE), "voornoemd"),
    (re.compile(r"\bvoorsz\.\b",     re.IGNORECASE), "voornoemd"),
    (re.compile(r"\bvoornt\.\b",     re.IGNORECASE), "voornoemd"),
    (re.compile(r"\bNB[:\.]?\b"),                      "Nota bene:"),

    # --- standalone y → ij (Dutch; guards against English loanwords) ---
    # Only fire when y is between Dutch-typical consonants or at word end
    (re.compile(r"(?<=[bcdfghjklmnpqrstvwx])y\b"), "ij"),

    # --- -ng ending normalisation (archaïc -nge → -ng) ---
    (re.compile(r"nge\b", re.IGNORECASE), "ng"),
]


def apply_rules(text: str) -> str:
    """Apply all rule-based normalization steps to a text string."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
