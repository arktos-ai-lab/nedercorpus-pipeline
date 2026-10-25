"""
Synthetic PAP→NL conversion for NederCorpus.

Converts Papiamento documents to Modern Dutch via LLM, capped at
floor(nl_count / ratio) documents to maintain the 20:1 NL:PAP balance.

Papiamento (PAP) is spoken in Aruba, Curaçao, and Bonaire. NederCorpus
includes Papiamento sources (coleccion_aruba, landscourant_aruba). This
module creates Dutch translations as additional NL training data.

Synthetic documents carry:
  meta.synthetic        = True
  meta.synthetic_method = "pap_to_nl"
  meta.language_variant = "NL"
  meta.source           = "{original_source}__synth"

LLM backends:
  local   — Qwen2.5 via local vLLM (http://localhost:8000/v1/completions)
  claude  — Anthropic Claude API (requires ANTHROPIC_API_KEY env var)
"""
from __future__ import annotations

import json
import logging
import math
import urllib.request
from pathlib import Path
from typing import Iterator

from pipeline.document import Document, read_jsonl, write_jsonl, make_id
from pipeline.schema import DocumentMeta

logger = logging.getLogger(__name__)

_PAP_VARIANTS = {"PAP"}
_NL_VARIANTS = {"NL", "NL-BE"}

_QWEN_URL = "http://localhost:8000/v1/completions"
_QWEN_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"

_PROMPT = (
    "Vertaal de volgende Papiamentse tekst naar correct modern Nederlands. "
    "Papiamento is een Creoolse taal van Aruba, Curaçao en Bonaire. "
    "Geef alleen de Nederlandse vertaling terug, geen uitleg.\n\n"
    "Papiamento:\n{text}\n\nNederlands:\n"
)


def _call_local(text: str, max_tokens: int = 1024, temperature: float = 0.2) -> str | None:
    prompt = _PROMPT.format(text=text[:2000])
    payload = json.dumps({
        "model": _QWEN_MODEL,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    try:
        req = urllib.request.Request(
            _QWEN_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        result = data["choices"][0]["text"].strip()
        return result if len(result) >= 20 else None
    except Exception as exc:
        logger.debug("[synth/pap_to_nl] local LLM call failed: %s", exc)
        return None


def _call_claude(text: str, model: str = "claude-haiku-4-5-20251001") -> str | None:
    try:
        import anthropic
    except ImportError:
        logger.error("[synth/pap_to_nl] anthropic package not installed: pip install anthropic")
        return None
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("[synth/pap_to_nl] ANTHROPIC_API_KEY not set")
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _PROMPT.format(text=text[:4000])
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        result = msg.content[0].text.strip()
        return result if len(result) >= 20 else None
    except Exception as exc:
        logger.debug("[synth/pap_to_nl] Claude API call failed: %s", exc)
        return None


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


def iter_pap_docs(langfiltered_dir: Path) -> Iterator[Document]:
    """Yield PAP-variant documents from the langfiltered directory."""
    for jsonl_file in sorted(langfiltered_dir.rglob("*.jsonl")):
        try:
            for doc in read_jsonl(jsonl_file):
                if doc.meta.language_variant in _PAP_VARIANTS:
                    yield doc
        except Exception:
            continue


def run(
    langfiltered_dir: Path,
    output_dir: Path,
    llm_backend: str = "local",
    claude_model: str = "claude-haiku-4-5-20251001",
    ratio: int = 20,
    dry_run: bool = False,
) -> int:
    """
    Convert Papiamento documents to Dutch and save as synthetic NL documents.

    Caps output at floor(nl_count / ratio) to maintain NL corpus balance.
    Returns number of synthetic documents written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "synth_pap_to_nl.jsonl"

    if dest.exists():
        existing = sum(1 for _ in read_jsonl(dest))
        logger.info("[synth/pap_to_nl] %s already exists with %d docs", dest.name, existing)
        return existing

    if dry_run:
        nl_count = count_nl_docs(langfiltered_dir)
        cap = math.floor(nl_count / ratio)
        logger.info(
            "[synth/pap_to_nl] [dry-run] NL docs: %d → cap: %d (ratio 1:%d)",
            nl_count, cap, ratio,
        )
        return 0

    logger.info("[synth/pap_to_nl] Counting NL docs in %s ...", langfiltered_dir)
    nl_count = count_nl_docs(langfiltered_dir)
    cap = math.floor(nl_count / ratio)
    logger.info("[synth/pap_to_nl] NL docs: %d → cap: %d (ratio 1:%d)", nl_count, cap, ratio)

    if cap == 0:
        logger.warning("[synth/pap_to_nl] No NL docs found — nothing to do")
        return 0

    call_llm = _call_local if llm_backend == "local" else \
               (lambda t: _call_claude(t, model=claude_model))

    written = 0
    skipped = 0
    converted: list[Document] = []

    for pap_doc in iter_pap_docs(langfiltered_dir):
        if written >= cap:
            break

        nl_text = call_llm(pap_doc.text)
        if not nl_text:
            skipped += 1
            continue

        new_meta = DocumentMeta(
            id=make_id(pap_doc.meta.source + "__synth"),
            source=pap_doc.meta.source + "__synth",
            source_url=pap_doc.meta.source_url,
            license=pap_doc.meta.license,
            publication_year=pap_doc.meta.publication_year,
            country=pap_doc.meta.country,
            domain=pap_doc.meta.domain,
            language_variant="NL",
            historical_flag=False,
            normalized_version=False,
            acquisition_date=pap_doc.meta.acquisition_date,
            synthetic=True,
            synthetic_method="pap_to_nl",
        )
        converted.append(Document(text=nl_text, meta=new_meta))
        written += 1

        if written % 100 == 0:
            logger.info("[synth/pap_to_nl] %d/%d converted (%d skipped)", written, cap, skipped)

    write_jsonl(iter(converted), dest)
    logger.info(
        "[synth/pap_to_nl] Done. %d synthetic docs written to %s (%d skipped)",
        written, dest, skipped,
    )
    return written
