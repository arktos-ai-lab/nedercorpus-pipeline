"""
Historical Dutch text normalization pipeline.

Two modes:

  Rule-based (default, free, instant):
    Character-level rules (long-s, ae→aa, gh→g, etc.) plus an optional
    archaic→modern word lexicon.  Handles well-known orthographic patterns
    but not irregular verb forms or syntactic archaisms.

  LLM-based (use_llm=True):
    Sends text chunks to a language model for genuine linguistic normalization.
    Handles everything rules cannot: irregular verb conjugations, archaic
    syntax, v/f confusion, historical word order, etc.

    The rule pass always runs first as a free pre-processor; the LLM then
    refines what remains — reducing token cost significantly.

    Backend options (llm_backend):
      "local"   — Local vLLM server (default, free, no API key needed)
                  Expects an OpenAI-compatible completions endpoint at
                  http://localhost:8000/v1/completions
                  (the Qwen 3 Coder model already running in your setup)

      "claude"  — Anthropic Claude API (requires ANTHROPIC_API_KEY env var)
                  Models: claude-sonnet-4-6 (recommended) or claude-opus-4-6

    Usage:
      # Local Qwen (free, runs on your machine)
      python -m pipeline normalize --use-llm

      # Claude via API key
      ANTHROPIC_API_KEY=sk-ant-... python -m pipeline normalize \
          --use-llm --llm-backend claude --llm-model claude-sonnet-4-6
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pipeline.normalize.rules import apply_rules
from pipeline.normalize.lexicon import load_lexicon, apply_lexicon
from pipeline.document import Document

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class NormalizeConfig:
    apply_rules:   bool       = True
    apply_lexicon: bool       = True
    lexicon_path:  Path | None = None

    # LLM normalization
    use_llm:      bool = False
    llm_backend:  str  = "local"          # "local" or "claude"
    llm_model:    str  = "Qwen/Qwen2.5-Coder-7B-Instruct"  # ignored for claude backend
    llm_url:      str  = "http://localhost:8000"            # local vLLM base URL
    claude_model: str  = "claude-sonnet-4-6"               # used when backend=claude

    # Chunking: max chars sent per LLM call
    llm_chunk_chars: int = 3_000
    llm_max_retries: int = 3


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = (
    "Je bent een expert in historisch Nederlands. Zet historische Nederlandse tekst "
    "om naar modern Nederlands. Behoud de betekenis en literaire stijl volledig. "
    "Moderniseer alleen spelling en verouderde woordvormen. "
    "Geef uitsluitend de genormaliseerde tekst terug, zonder commentaar."
)

_USER_TMPL = "Normaliseer:\n\n{text}"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunks(text: str, max_chars: int) -> list[str]:
    """Split text into paragraph-aligned chunks of at most max_chars."""
    paragraphs = text.split("\n\n")
    result: list[str] = []
    current: list[str] = []
    length = 0
    for para in paragraphs:
        plen = len(para) + 2
        if current and length + plen > max_chars:
            result.append("\n\n".join(current))
            current, length = [], 0
        current.append(para)
        length += plen
    if current:
        result.append("\n\n".join(current))
    return result


# ---------------------------------------------------------------------------
# Local vLLM backend (OpenAI-compatible /v1/completions)
# ---------------------------------------------------------------------------

def _local_normalize_chunk(chunk: str, config: NormalizeConfig) -> str:
    """Call the local vLLM completions endpoint to normalize one chunk."""
    import time

    prompt = f"{_SYSTEM}\n\n{_USER_TMPL.format(text=chunk)}"
    payload = json.dumps({
        "model": config.llm_model,
        "prompt": prompt,
        "max_tokens": 2048,
        "temperature": 0.1,   # low temperature for deterministic normalization
    }).encode()

    url = config.llm_url.rstrip("/") + "/v1/completions"

    for attempt in range(config.llm_max_retries):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["text"].strip()
        except Exception as exc:
            if attempt == config.llm_max_retries - 1:
                logger.warning(
                    "Local LLM normalization failed after %d retries: %s — returning original",
                    config.llm_max_retries, exc,
                )
                return chunk
            wait = 2.0 ** attempt
            logger.debug("Retry %d for local LLM (%.1fs): %s", attempt + 1, wait, exc)
            time.sleep(wait)
    return chunk


# ---------------------------------------------------------------------------
# Claude API backend
# ---------------------------------------------------------------------------

def _claude_normalize_chunk(chunk: str, config: NormalizeConfig) -> str:
    """Call the Anthropic Claude API to normalize one chunk."""
    import time

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error(
            "ANTHROPIC_API_KEY not set. Use --llm-backend local for the local Qwen model."
        )
        return chunk

    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed. Run: pip install anthropic")
        return chunk

    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(config.llm_max_retries):
        try:
            msg = client.messages.create(
                model=config.claude_model,
                max_tokens=2048,
                system=_SYSTEM,
                messages=[{"role": "user", "content": _USER_TMPL.format(text=chunk)}],
            )
            return msg.content[0].text
        except Exception as exc:
            if attempt == config.llm_max_retries - 1:
                logger.warning("Claude normalization failed: %s — returning original", exc)
                return chunk
            time.sleep(2.0 ** attempt)
    return chunk


# ---------------------------------------------------------------------------
# Rule-based normalization
# ---------------------------------------------------------------------------

def normalize_text_rules(text: str, config: NormalizeConfig) -> str:
    if config.apply_rules:
        text = apply_rules(text)
    if config.apply_lexicon:
        lexicon = (
            load_lexicon(config.lexicon_path) if config.lexicon_path else load_lexicon()
        )
        text = apply_lexicon(text, lexicon)
    return text


# ---------------------------------------------------------------------------
# LLM normalization (dispatches to local or claude)
# ---------------------------------------------------------------------------

def normalize_text_llm(text: str, config: NormalizeConfig) -> str:
    """Normalize text using the configured LLM backend."""
    parts = _chunks(text, config.llm_chunk_chars)
    logger.debug(
        "LLM normalization (%s): %d chunks for %d chars", config.llm_backend, len(parts), len(text)
    )

    normalized: list[str] = []
    for chunk in parts:
        if config.llm_backend == "claude":
            normalized.append(_claude_normalize_chunk(chunk, config))
        else:
            normalized.append(_local_normalize_chunk(chunk, config))

    return "\n\n".join(normalized)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_text(text: str, config: NormalizeConfig | None = None) -> str:
    """
    Normalize historical Dutch text.

    Rule pass first (always), then optional LLM pass.
    """
    cfg = config or NormalizeConfig()
    text = normalize_text_rules(text, cfg)
    if cfg.use_llm:
        text = normalize_text_llm(text, cfg)
    return text


def normalize_document(doc: Document, config: NormalizeConfig | None = None) -> Document:
    """
    Normalize a Document marked as historical Dutch.

    Non-historical documents are returned unchanged.
    Preserves original text in doc.original_text.
    """
    if not doc.meta.historical_flag:
        return doc
    normalized = normalize_text(doc.text, config)
    return Document(
        text=normalized,
        meta=_updated_meta(doc.meta),
        original_text=doc.original_text or doc.text,
    )


def _updated_meta(meta):
    from dataclasses import replace
    return replace(meta, normalized_version=True)
