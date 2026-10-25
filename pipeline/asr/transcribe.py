"""
faster-whisper transcription for NederCorpus podcast sources.

Transcribes .mp3 / .m4a audio files → .txt plain text using
faster-whisper with the large-v3 model and language=nl.

The model is loaded once per process and reused across all files.
On CUDA-capable hardware it runs automatically on GPU; CPU is used otherwise.

Output: {stem}.txt written alongside the audio file, with the transcript
as a single paragraph (segments joined with spaces). The audio file is
kept on disk so the pipeline can be re-run with a different model.

References:
  https://github.com/SYSTRAN/faster-whisper  (MIT license)
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Sources that contain podcast audio; only these are processed by default.
_PODCAST_SOURCES = {"uvn_podcast", "uvv_podcast", "radio_oranje"}

# Audio extensions to transcribe
_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}


def _load_model(model_size: str = "large-v3", device: str = "auto", compute_type: str = "auto"):
    """
    Load and return a faster-whisper WhisperModel.

    device="auto"       → CUDA if available, else CPU
    compute_type="auto" → float16 on GPU, int8 on CPU
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ImportError(
            "faster-whisper is required for the ASR stage. "
            "Install with: pip install faster-whisper"
        ) from exc

    # Resolve auto device/compute_type
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    logger.info("[asr] Loading faster-whisper %s (device=%s, compute=%s)", model_size, device, compute_type)
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def _transcribe_file(model, audio_path: Path, language: str = "nl") -> str | None:
    """
    Transcribe a single audio file. Returns plain text or None on failure.

    Segments are joined into a single paragraph. Whisper large-v3 produces
    accurate Dutch output including disfluencies and domain vocabulary.
    """
    try:
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,         # skip silent parts
            vad_parameters={"min_silence_duration_ms": 500},
        )
        detected = info.language
        if detected not in ("nl", "af", "fy"):
            logger.warning(
                "[asr] %s detected language=%s (expected nl/af/fy) — transcribing anyway",
                audio_path.name, detected,
            )

        texts = [seg.text.strip() for seg in segments if seg.text.strip()]
        if not texts:
            return None
        return " ".join(texts)
    except Exception as exc:
        logger.error("[asr] Transcription failed for %s: %s", audio_path.name, exc)
        return None


def transcribe_dir(
    raw_dir: Path,
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    language: str = "nl",
    model=None,
) -> int:
    """
    Transcribe all audio files in raw_dir that lack a .txt transcript.

    Writes {stem}.txt alongside each audio file. Skips files already
    transcribed (idempotent).

    Args:
        raw_dir:      Source raw directory (e.g. data/raw/uvn_podcast/).
        model_size:   Whisper model size (default: large-v3).
        device:       "auto", "cuda", or "cpu".
        compute_type: "auto", "float16", or "int8".
        language:     BCP-47 language code (default: "nl").
        model:        Pre-loaded WhisperModel (reused across dirs to avoid
                      repeated model loading). Loaded on first call if None.

    Returns:
        Number of files transcribed.
    """
    audio_files = [
        f for f in sorted(raw_dir.iterdir())
        if f.suffix.lower() in _AUDIO_EXTS and not f.name.startswith("_")
    ]

    if not audio_files:
        logger.info("[asr] No audio files in %s", raw_dir)
        return 0

    # Count files already done
    pending = [f for f in audio_files if not f.with_suffix(".txt").exists()]
    if not pending:
        logger.info("[asr] All %d files already transcribed in %s", len(audio_files), raw_dir.name)
        return 0

    logger.info("[asr] %s: %d audio files, %d pending", raw_dir.name, len(audio_files), len(pending))

    if model is None:
        model = _load_model(model_size, device, compute_type)

    transcribed = 0
    for audio_path in pending:
        txt_path = audio_path.with_suffix(".txt")
        logger.info("[asr] Transcribing %s …", audio_path.name)

        text = _transcribe_file(model, audio_path, language=language)
        if not text:
            logger.warning("[asr] Empty transcript for %s — skipping", audio_path.name)
            continue

        # Clean up and normalise whitespace
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # Prepend title from sidecar if present
        json_path = audio_path.with_suffix(".json")
        if json_path.exists():
            try:
                sidecar = json.loads(json_path.read_text(encoding="utf-8"))
                title = sidecar.get("title", "")
                if title:
                    text = title + "\n\n" + text
            except (json.JSONDecodeError, OSError):
                pass

        txt_path.write_text(text, encoding="utf-8")
        transcribed += 1
        logger.info("[asr] ✓ %s → %d chars", audio_path.name, len(text))

    logger.info("[asr] %s: transcribed %d / %d files", raw_dir.name, transcribed, len(pending))
    return transcribed


def run_asr(
    raw_dir: Path,
    sources: list[str] | None = None,
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
) -> dict[str, int]:
    """
    Run ASR transcription across all podcast source directories.

    Loads the Whisper model once and reuses it across sources.

    Args:
        raw_dir:      Root raw data directory (contains subdirs per source).
        sources:      List of source names to process; None = all podcast sources.
        model_size:   Whisper model size (default: large-v3).
        device:       "auto", "cuda", or "cpu".
        compute_type: "auto", "float16", or "int8".

    Returns:
        dict mapping source name → files transcribed.
    """
    target_sources = set(sources) if sources else _PODCAST_SOURCES
    counts: dict[str, int] = {}
    model = None  # loaded lazily on first transcription

    for source_dir in sorted(raw_dir.iterdir()):
        if not source_dir.is_dir():
            continue
        if source_dir.name not in target_sources:
            continue

        # Determine language for this source
        lang = "nl"  # default Dutch; both UvN and UvV are Dutch

        if model is None:
            model = _load_model(model_size, device, compute_type)

        # Some sources store audio in an audio/ subdirectory (e.g. radio_oranje)
        audio_subdir = source_dir / "audio"
        transcribe_target = audio_subdir if audio_subdir.is_dir() else source_dir

        n = transcribe_dir(
            transcribe_target,
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            language=lang,
            model=model,
        )
        counts[source_dir.name] = n

    total = sum(counts.values())
    logger.info("[asr] Done. Total transcribed: %d across %d sources", total, len(counts))
    return counts
