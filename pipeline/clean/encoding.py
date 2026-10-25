"""Encoding normalization utilities."""
from __future__ import annotations

import unicodedata


def to_utf8(raw: bytes, hint: str = "utf-8") -> str:
    """Decode bytes to str, trying the hinted encoding then common fallbacks."""
    for enc in [hint, "utf-8", "iso-8859-1", "windows-1252"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_unicode(text: str) -> str:
    """Apply NFC normalization and remove control characters."""
    text = unicodedata.normalize("NFC", text)
    return "".join([ch for ch in text if unicodedata.category(ch) != "Cc" or ch in "\n\t"])
