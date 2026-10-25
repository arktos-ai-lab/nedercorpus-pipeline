"""Lexicon-based normalization for historical Dutch text."""
from __future__ import annotations

import json
from pathlib import Path

# Path to the lexicon JSON: maps archaic form -> modern form
DEFAULT_LEXICON = Path(__file__).parent / "data" / "lexicon.json"


def load_lexicon(path: Path = DEFAULT_LEXICON) -> dict[str, str]:
    """Load a JSON lexicon mapping archaic words to modern equivalents."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    """Replace archaic word forms using the lexicon (case-insensitive, whole-word)."""
    if not lexicon:
        return text
    import re
    def replace(match: re.Match) -> str:
        word = match.group(0)
        return lexicon.get(word.lower(), word)
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in lexicon) + r")\b", re.IGNORECASE)
    return pattern.sub(replace, text)
