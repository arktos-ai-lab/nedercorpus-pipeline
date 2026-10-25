"""
Synthetic spoken-form Dutch via TTS → Whisper round-trip normalization.

PURPOSE
-------
Formal written Dutch contains many elements that are spoken differently from
how they are written: numbers, abbreviations, dates, legal references, etc.

Examples of written → spoken divergence:
  "art. 3 lid 1 sub b"       → "artikel drie lid één sub b"
  "dhr. J. de Vries"         → "de heer J. de Vries"
  "€ 1.250,00"               → "twaalfhonderdvijftig euro"
  "31-03-2022"               → "eenendertig maart tweeduizend tweeëntwintig"
  "i.v.m."                   → "in verband met"
  "m.b.t."                   → "met betrekking tot"

PIPELINE
--------
1. Read Documents from data/parsed/ (any source)
2. Send text through a Dutch TTS model (piper-tts with nl_NL voice)
   → Produces WAV audio (~22kHz)
3. Transcribe WAV with OpenAI Whisper (Dutch)
   → Produces spoken-form transcription
4. Pair (original_written, whisper_transcript) as a normalization pair
5. Write output as JSONL: {written, spoken, source, doc_id}

The synthetic pairs can be used to:
- Train a written→spoken normalizer (for TTS pre-processing)
- Train a spoken→written normalizer (for ASR post-processing)
- Augment the main corpus with spoken-register Dutch

REQUIREMENTS
------------
  pip install piper-tts openai-whisper soundfile numpy

Piper TTS Dutch voices (Apache 2.0 license):
  nl_NL-mls-medium     — general Dutch (male, medium quality)
  nl_NL-nathalie-x_low — general Dutch (female, low quality, fast)
  nl_BE-nathalie-x_low — Belgian Dutch (female)

Download voice: python -m piper.download nl_NL-mls-medium

LICENSE OF SYNTHETIC DATA
--------------------------
The synthetic (written, spoken) pairs derive from the source documents.
The "spoken" form is a transformation of the original text, so it inherits
the source document's license. Only PD/CC0/CC-BY source documents are
processed by default.

Usage:
  python -m pipeline.synth.tts_normalize \\
    --input  data/parsed/ \\
    --output data/synth/tts_pairs/ \\
    --voice  nl_NL-mls-medium \\
    --max-docs 10000
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Only process documents from these permissive licenses
_ALLOWED_LICENSES = {"PD", "CC0", "CC-BY", "CC-BY-SA"}

# Documents longer than this are chunked (TTS has practical length limits)
_MAX_CHUNK_CHARS = 500

# Minimum length of output pair to keep
_MIN_PAIR_CHARS = 80


def _check_deps() -> tuple[bool, bool]:
    """Check if piper-tts and whisper are importable."""
    has_piper = False
    has_whisper = False
    try:
        import piper  # noqa: F401
        has_piper = True
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401
        has_whisper = True
    except ImportError:
        pass
    return has_piper, has_whisper


def _chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """
    Split text into chunks suitable for TTS synthesis.

    Splits on sentence boundaries (period + space) to avoid cutting mid-sentence.
    """
    chunks: list[str] = []
    current = ""
    for sentence in text.replace("\n", " ").split(". "):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 2 <= max_chars:
            current = f"{current}. {sentence}" if current else sentence
        else:
            if current:
                chunks.append(current.strip())
            current = sentence
    if current:
        chunks.append(current.strip())
    return [c for c in chunks if len(c) >= 20]


def _tts_chunk(text: str, voice: str, wav_path: Path) -> bool:
    """
    Synthesise text to WAV using piper-tts CLI.

    Returns True on success. Uses subprocess to avoid piper's global model state.
    """
    cmd = [
        sys.executable, "-m", "piper",
        "--model", voice,
        "--output_file", str(wav_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 44
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _transcribe_wav(wav_path: Path, whisper_model) -> str:
    """Transcribe a WAV file with Whisper, returning Dutch text."""
    try:
        result = whisper_model.transcribe(
            str(wav_path),
            language="nl",
            fp16=False,
            temperature=0,
        )
        return result.get("text", "").strip()
    except Exception as exc:
        logger.debug("Whisper transcription failed: %s", exc)
        return ""


def run(
    input_dir: Path,
    output_dir: Path,
    voice: str = "nl_NL-mls-medium",
    whisper_model_name: str = "small",
    max_docs: int = 10_000,
    allowed_licenses: set[str] | None = None,
    dry_run: bool = False,
) -> None:
    """
    Run TTS → Whisper round-trip normalization on parsed Dutch documents.

    Produces JSONL pairs in output_dir/tts_pairs_NNNNNN.jsonl.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed = allowed_licenses or _ALLOWED_LICENSES

    if dry_run:
        logger.info("[tts_normalize] [dry-run] skipping")
        return

    has_piper, has_whisper = _check_deps()
    if not has_piper:
        logger.error(
            "[tts_normalize] piper-tts not installed. Run: pip install piper-tts"
        )
        return
    if not has_whisper:
        logger.error(
            "[tts_normalize] openai-whisper not installed. Run: pip install openai-whisper"
        )
        return

    import whisper as _whisper
    logger.info("[tts_normalize] Loading Whisper '%s' model…", whisper_model_name)
    whisper_model = _whisper.load_model(whisper_model_name)

    pairs_written = 0
    docs_processed = 0
    file_num = 0
    out_file = output_dir / f"tts_pairs_{file_num:06d}.jsonl"
    out_handle = out_file.open("a", encoding="utf-8")

    jsonl_files = sorted(input_dir.glob("*.jsonl"))
    logger.info("[tts_normalize] Found %d JSONL files in %s", len(jsonl_files), input_dir)

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "chunk.wav"

        for jsonl_file in jsonl_files:
            if docs_processed >= max_docs:
                break
            try:
                lines = jsonl_file.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue

            for line in lines:
                if docs_processed >= max_docs:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    continue

                meta = doc.get("meta", {})
                license_ = meta.get("license", "")
                if license_ not in allowed:
                    continue

                text = doc.get("text", "").strip()
                if len(text) < 100:
                    continue

                doc_id = meta.get("id", "")
                source = meta.get("source", "")
                chunks = _chunk_text(text)

                for chunk in chunks[:5]:  # max 5 chunks per doc
                    if not _tts_chunk(chunk, voice, wav_path):
                        continue

                    spoken = _transcribe_wav(wav_path, whisper_model)
                    if not spoken or len(spoken) < _MIN_PAIR_CHARS:
                        continue

                    pair = {
                        "written":  chunk,
                        "spoken":   spoken,
                        "source":   source,
                        "doc_id":   doc_id,
                        "license":  license_,
                        "voice":    voice,
                        "whisper":  whisper_model_name,
                    }
                    out_handle.write(json.dumps(pair, ensure_ascii=False) + "\n")
                    pairs_written += 1

                    if pairs_written % 1000 == 0:
                        out_handle.close()
                        file_num += 1
                        out_file = output_dir / f"tts_pairs_{file_num:06d}.jsonl"
                        out_handle = out_file.open("a", encoding="utf-8")
                        logger.info("[tts_normalize] %d pairs written", pairs_written)

                    if wav_path.exists():
                        wav_path.unlink()

                docs_processed += 1

    out_handle.close()
    logger.info(
        "[tts_normalize] Done. %d docs → %d (written, spoken) pairs.",
        docs_processed, pairs_written,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="TTS → Whisper round-trip: generate spoken-form Dutch pairs"
    )
    parser.add_argument("--input",   required=True, type=Path,
                        help="Directory of parsed JSONL documents")
    parser.add_argument("--output",  required=True, type=Path,
                        help="Output directory for (written, spoken) pairs")
    parser.add_argument("--voice",   default="nl_NL-mls-medium",
                        help="Piper TTS voice model name")
    parser.add_argument("--whisper-model", default="small",
                        help="Whisper model size: tiny/base/small/medium/large")
    parser.add_argument("--max-docs", type=int, default=10_000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(
        args.input, args.output,
        voice=args.voice,
        whisper_model_name=args.whisper_model,
        max_docs=args.max_docs,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
