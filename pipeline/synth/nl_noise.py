"""
Synthetic Dutch noise generation for NederCorpus.

Produces noise-augmented variants of clean Dutch documents at three levels.
Useful for training robust NLP models that handle real-world text quality.

Noise levels:
  light  — OCR-like errors, missing diacritics, occasional letter swaps.
            Approximates lightly degraded scanned documents.
  heavy  — Stronger character-level noise, Dutch-specific dt/d/t confusion,
            informal contractions, punctuation drops.
            Approximates informal typed text (forum posts, short messages).
  ultra  — Very heavy perturbation: word abbreviations, letter repetition,
            SMS-style writing, random capitalisation, word boundary errors.
            Approximates social media / SMS / very informal contexts.

Cap: floor(nl_count / ratio) per noise level (default ratio=20).
Each level has its own cap so adding more noise levels doesn't inflate totals.

Synthetic documents carry:
  meta.synthetic        = True
  meta.synthetic_method = "nl_noise_{level}"   (nl_noise_light / _heavy / _ultra)
  meta.language_variant = "NL"
  meta.source           = "{original_source}__noise_{level}"
"""
from __future__ import annotations

import logging
import math
import random
import re
import unicodedata
from pathlib import Path
from typing import Iterator, Literal

from pipeline.document import Document, read_jsonl, write_jsonl, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

_NL_VARIANTS = {"NL", "NL-BE"}

NoiseLevel = Literal["light", "heavy", "ultra"]

# ---------------------------------------------------------------------------
# Character-level noise helpers
# ---------------------------------------------------------------------------

# Dutch diacritics → plain ASCII (OCR/typing errors)
_DIACRITIC_MAP = {
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "ï": "i", "í": "i", "î": "i",
    "ó": "o", "ô": "o", "ö": "o",
    "ú": "u", "ü": "u", "û": "u",
    "á": "a", "â": "a", "ä": "a",
    "ñ": "n",
}

# Common Dutch dt/t/d spelling confusions (word-final)
_DT_CONFUSIONS = [
    (re.compile(r"\b(\w+)dt\b"), r"\1t"),
    (re.compile(r"\b(\w+[aeiou])t\b"), r"\1dt"),
    (re.compile(r"\bword\b"), "wordt"),
    (re.compile(r"\bwordt\b"), "word"),
]

# Keyboard-adjacent character swaps (QWERTY layout)
_NEAR_KEYS: dict[str, str] = {
    "a": "sqwze", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsrd",
    "f": "drtgvc", "g": "ftyhn", "h": "gyujnb", "i": "uojk", "j": "huikn",
    "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "ipkl",
    "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy",
    "u": "yihj", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tgu",
    "z": "asx",
}


def _near_key(ch: str) -> str:
    """Return a random adjacent key for a character (or itself)."""
    candidates = _NEAR_KEYS.get(ch.lower(), "")
    if not candidates:
        return ch
    replacement = random.choice(candidates)
    return replacement.upper() if ch.isupper() else replacement


def _drop_diacritics(text: str, rate: float) -> str:
    """Replace diacritics with plain ASCII at the given probability."""
    result = []
    for ch in text:
        if ch in _DIACRITIC_MAP and random.random() < rate:
            result.append(_DIACRITIC_MAP[ch])
        else:
            result.append(ch)
    return "".join(result)


def _swap_chars(text: str, rate: float) -> str:
    """Swap adjacent characters at the given rate."""
    chars = list(text)
    i = 0
    while i < len(chars) - 1:
        if chars[i].isalpha() and random.random() < rate:
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
            i += 2
        else:
            i += 1
    return "".join(chars)


def _delete_chars(text: str, rate: float) -> str:
    """Delete random characters at the given rate."""
    return "".join(ch for ch in text if not (ch.isalpha() and random.random() < rate))


def _substitute_chars(text: str, rate: float) -> str:
    """Replace characters with near-key alternatives at the given rate."""
    return "".join(
        _near_key(ch) if ch.isalpha() and random.random() < rate else ch
        for ch in text
    )


def _double_chars(text: str, rate: float) -> str:
    """Accidentally double characters (keyboard hold) at the given rate."""
    result = []
    for ch in text:
        result.append(ch)
        if ch.isalpha() and random.random() < rate:
            result.append(ch)
    return "".join(result)


def _apply_dt_confusion(text: str, rate: float) -> str:
    """Apply Dutch dt/t/d spelling confusion at word level."""
    words = text.split()
    result = []
    for word in words:
        if random.random() < rate:
            for pattern, repl in _DT_CONFUSIONS:
                if pattern.search(word):
                    word = pattern.sub(repl, word, count=1)
                    break
        result.append(word)
    return " ".join(result)


def _drop_spaces(text: str, rate: float) -> str:
    """Merge adjacent words (drop spaces) at the given rate."""
    result = []
    for ch in text:
        if ch == " " and random.random() < rate:
            pass  # drop space = merge words
        else:
            result.append(ch)
    return "".join(result)


def _insert_spaces(text: str, rate: float) -> str:
    """Insert spurious spaces inside words at the given rate."""
    result = []
    for ch in text:
        result.append(ch)
        if ch.isalpha() and random.random() < rate:
            result.append(" ")
    return "".join(result)


def _abbreviate_words(text: str, rate: float) -> str:
    """Abbreviate words by dropping vowels (SMS style)."""
    words = text.split()
    result = []
    for word in words:
        if len(word) > 4 and word.isalpha() and random.random() < rate:
            # Keep first char + consonants + last char
            inner = word[1:-1]
            abbreviated = word[0] + re.sub(r"[aeiouAEIOU]", "", inner) + word[-1]
            result.append(abbreviated if len(abbreviated) >= 2 else word)
        else:
            result.append(word)
    return " ".join(result)


def _random_caps(text: str, rate: float) -> str:
    """Randomly capitalise/lowercase characters."""
    return "".join(
        ch.upper() if ch.isalpha() and random.random() < rate else ch
        for ch in text
    )


def _drop_punctuation(text: str, rate: float) -> str:
    """Drop punctuation marks at the given rate."""
    return "".join(
        ch for ch in text
        if not (ch in ".,;:!?\"'()-" and random.random() < rate)
    )


# ---------------------------------------------------------------------------
# Per-level noise functions
# ---------------------------------------------------------------------------

def _add_noise_light(text: str, rng: random.Random) -> str:
    """Light noise: diacritic drops, occasional character errors."""
    _random = rng.random

    # Drop diacritics ~15% of the time
    result = []
    for ch in text:
        if ch in _DIACRITIC_MAP and _random() < 0.15:
            result.append(_DIACRITIC_MAP[ch])
        else:
            result.append(ch)
    text = "".join(result)

    # Swap adjacent chars 1%
    chars = list(text)
    i = 0
    while i < len(chars) - 1:
        if chars[i].isalpha() and _random() < 0.01:
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
            i += 2
        else:
            i += 1
    text = "".join(chars)

    # Delete random char 0.5%
    text = "".join(ch for ch in text if not (ch.isalpha() and _random() < 0.005))
    return text


def _add_noise_heavy(text: str, rng: random.Random) -> str:
    """Heavy noise: OCR errors + Dutch dt/d/t confusion + informal contractions."""
    _random = rng.random

    # Diacritics 40%
    result = []
    for ch in text:
        if ch in _DIACRITIC_MAP and _random() < 0.4:
            result.append(_DIACRITIC_MAP[ch])
        else:
            result.append(ch)
    text = "".join(result)

    # Char substitution 2%
    text = "".join(
        _near_key(ch) if ch.isalpha() and _random() < 0.02 else ch
        for ch in text
    )

    # Char deletion 1%
    text = "".join(ch for ch in text if not (ch.isalpha() and _random() < 0.01))

    # Double chars 0.5%
    result = []
    for ch in text:
        result.append(ch)
        if ch.isalpha() and _random() < 0.005:
            result.append(ch)
    text = "".join(result)

    # Dutch dt/d/t confusion 3% per word
    words = text.split()
    out = []
    for word in words:
        if _random() < 0.03:
            for pattern, repl in _DT_CONFUSIONS:
                if pattern.search(word):
                    word = pattern.sub(repl, word, count=1)
                    break
        out.append(word)
    text = " ".join(out)

    # Drop punctuation 10%
    text = "".join(
        ch for ch in text
        if not (ch in ".,;:!?" and _random() < 0.10)
    )

    return text


def _add_noise_ultra(text: str, rng: random.Random) -> str:
    """Ultra noise: SMS-style, aggressive abbreviation, random caps, word merges."""
    _random = rng.random

    # Strip all diacritics
    result = []
    for ch in text:
        if ch in _DIACRITIC_MAP:
            result.append(_DIACRITIC_MAP[ch])
        else:
            result.append(ch)
    text = "".join(result)

    # Char substitution 5%
    text = "".join(
        _near_key(ch) if ch.isalpha() and _random() < 0.05 else ch
        for ch in text
    )

    # Char deletion 2%
    text = "".join(ch for ch in text if not (ch.isalpha() and _random() < 0.02))

    # Double chars 1%
    result = []
    for ch in text:
        result.append(ch)
        if ch.isalpha() and _random() < 0.01:
            result.append(ch)
    text = "".join(result)

    # Abbreviate words 15%
    words = text.split()
    out = []
    for word in words:
        if len(word) > 4 and word.isalpha() and _random() < 0.15:
            inner = word[1:-1]
            abbr = word[0] + re.sub(r"[aeiouAEIOU]", "", inner) + word[-1]
            out.append(abbr if len(abbr) >= 2 else word)
        else:
            out.append(word)
    text = " ".join(out)

    # Random capitalisation 3%
    text = "".join(
        ch.upper() if ch.isalpha() and _random() < 0.03 else ch
        for ch in text
    )

    # Drop spaces (merge words) 1%
    result = []
    for ch in text:
        if ch == " " and _random() < 0.01:
            pass
        else:
            result.append(ch)
    text = "".join(result)

    # Insert spurious spaces 0.5%
    result = []
    for ch in text:
        result.append(ch)
        if ch.isalpha() and _random() < 0.005:
            result.append(" ")
    text = "".join(result)

    # Drop punctuation 25%
    text = "".join(
        ch for ch in text
        if not (ch in ".,;:!?\"'()-" and _random() < 0.25)
    )

    return text


_NOISE_FUNCS = {
    "light": _add_noise_light,
    "heavy": _add_noise_heavy,
    "ultra": _add_noise_ultra,
}


# ---------------------------------------------------------------------------
# Document iteration and counting
# ---------------------------------------------------------------------------

def count_nl_docs(langfiltered_dir: Path) -> int:
    """Count NL/NL-BE documents in the langfiltered directory."""
    count = 0
    for jsonl_file in sorted(langfiltered_dir.rglob("*.jsonl")):
        try:
            for doc in read_jsonl(jsonl_file):
                if doc.meta.language_variant in _NL_VARIANTS:
                    count += 1
        except Exception:
            continue
    return count


def iter_nl_docs(langfiltered_dir: Path) -> Iterator[Document]:
    """Yield NL/NL-BE documents from the langfiltered directory."""
    for jsonl_file in sorted(langfiltered_dir.rglob("*.jsonl")):
        try:
            for doc in read_jsonl(jsonl_file):
                if doc.meta.language_variant in _NL_VARIANTS:
                    yield doc
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    langfiltered_dir: Path,
    output_dir: Path,
    levels: list[NoiseLevel] | None = None,
    ratio: int = 20,
    seed: int = 42,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Generate noise-augmented Dutch documents at the specified noise levels.

    Each level has its own cap of floor(nl_count / ratio) documents.
    Returns dict of {level: docs_written}.

    Args:
        langfiltered_dir: Directory containing langfiltered JSONL files.
        output_dir:       Output directory for synthetic JSONL files.
        levels:           List of noise levels (default: all three).
        ratio:            NL:noisy cap ratio per level (default 20).
        seed:             Random seed for reproducibility.
        dry_run:          Log planned output without writing.
    """
    if levels is None:
        levels = ["light", "heavy", "ultra"]

    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, int] = {}

    logger.info("[synth/nl_noise] Counting NL docs in %s ...", langfiltered_dir)
    nl_count = count_nl_docs(langfiltered_dir)
    cap = math.floor(nl_count / ratio)
    logger.info("[synth/nl_noise] NL docs: %d → cap per level: %d (ratio 1:%d)", nl_count, cap, ratio)

    if cap == 0:
        logger.warning("[synth/nl_noise] No NL docs found — nothing to do")
        return {lv: 0 for lv in levels}

    if dry_run:
        for level in levels:
            logger.info("[synth/nl_noise] [dry-run] %s: would write %d docs", level, cap)
            results[level] = 0
        return results

    for level in levels:
        dest = output_dir / f"synth_nl_noise_{level}.jsonl"
        if dest.exists():
            existing = sum(1 for _ in read_jsonl(dest))
            logger.info("[synth/nl_noise] %s already exists (%d docs)", dest.name, existing)
            results[level] = existing
            continue

        rng = random.Random(seed + hash(level))
        noise_fn = _NOISE_FUNCS[level]
        converted: list[Document] = []
        written = 0

        for nl_doc in iter_nl_docs(langfiltered_dir):
            if written >= cap:
                break

            noisy_text = noise_fn(nl_doc.text, rng)
            if not noisy_text or not noisy_text.strip():
                continue

            method = f"nl_noise_{level}"
            new_meta = DocumentMeta(
                id=make_id(nl_doc.meta.source + f"__noise_{level}"),
                source=nl_doc.meta.source + f"__noise_{level}",
                source_url=nl_doc.meta.source_url,
                license=nl_doc.meta.license,
                publication_year=nl_doc.meta.publication_year,
                country=nl_doc.meta.country,
                domain=nl_doc.meta.domain,
                language_variant="NL",
                historical_flag=nl_doc.meta.historical_flag,
                normalized_version=False,
                acquisition_date=nl_doc.meta.acquisition_date,
                synthetic=True,
                synthetic_method=method,
            )
            converted.append(Document(text=noisy_text, meta=new_meta))
            written += 1

            if written % 10_000 == 0:
                logger.info("[synth/nl_noise] %s: %d/%d written", level, written, cap)

        write_jsonl(iter(converted), dest)
        logger.info("[synth/nl_noise] %s: %d docs written to %s", level, written, dest)
        results[level] = written

    return results
