"""
Tests for pipeline/asr/transcribe.py

faster-whisper is an optional dependency; these tests mock the model so
they run without GPU or a downloaded Whisper checkpoint.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pipeline.asr.transcribe import transcribe_dir, run_asr, _AUDIO_EXTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_info(language: str = "nl") -> SimpleNamespace:
    return SimpleNamespace(language=language)


def _make_segment(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def _make_mock_model(segments_text: list[str], language: str = "nl"):
    """Return a mock with a .transcribe() method that yields fake segments."""
    info = _make_info(language)
    segments = [_make_segment(t) for t in segments_text]

    model = MagicMock()
    model.transcribe.return_value = (segments, info)
    return model


def _make_audio(p: Path, ext: str = ".mp3") -> Path:
    f = p / f"episode_01{ext}"
    f.write_bytes(b"FAKE_AUDIO_DATA")
    return f


def _make_sidecar(p: Path, stem: str, title: str = "Test Episode") -> Path:
    j = p / f"{stem}.json"
    j.write_text(json.dumps({
        "source": "uvn_podcast",
        "title": title,
        "ep_id": stem,
        "license": "CC-BY",
        "country": "NL",
    }), encoding="utf-8")
    return j


# ---------------------------------------------------------------------------
# transcribe_dir — unit tests with mocked model
# ---------------------------------------------------------------------------

def test_transcribe_dir_writes_txt():
    """transcribe_dir writes {stem}.txt from model output."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        audio = _make_audio(p)
        _make_sidecar(p, audio.stem, title="Episode één")

        model = _make_mock_model([
            " Welkom bij deze aflevering.",
            " We bespreken vandaag sterrenkunde.",
            " Sterren zijn enorme ballen van plasma.",
        ])

        n = transcribe_dir(p, model=model)

        assert n == 1
        txt = p / "episode_01.txt"
        assert txt.exists()
        content = txt.read_text(encoding="utf-8")
        assert "sterrenkunde" in content
        # Title should be prepended from sidecar
        assert "Episode één" in content


def test_transcribe_dir_skips_already_done():
    """Does not re-transcribe if .txt already exists."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        audio = _make_audio(p)
        # Pre-existing .txt
        (p / "episode_01.txt").write_text("Bestaande transcriptie.", encoding="utf-8")

        model = _make_mock_model(["Dit zou niet opgeslagen mogen worden."])
        n = transcribe_dir(p, model=model)

    assert n == 0
    model.transcribe.assert_not_called()


def test_transcribe_dir_no_audio():
    """Returns 0 when no audio files present."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "notes.txt").write_text("Geen audio hier.", encoding="utf-8")
        n = transcribe_dir(p, model=MagicMock())

    assert n == 0


def test_transcribe_dir_skips_underscore_files():
    """Files starting with _ are skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "_meta.mp3").write_bytes(b"FAKE")
        (p / "real_episode.mp3").write_bytes(b"FAKE")

        model = _make_mock_model(["Goedemorgen, dit is een test."])
        n = transcribe_dir(p, model=model)

        assert n == 1
        assert (p / "real_episode.txt").exists()
        assert not (p / "_meta.txt").exists()


def test_transcribe_dir_skips_empty_output():
    """Empty transcript from model → no .txt written."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_audio(p)

        model = _make_mock_model([])  # no segments
        n = transcribe_dir(p, model=model)

        assert n == 0
        assert not (p / "episode_01.txt").exists()


def test_transcribe_dir_multiple_extensions():
    """Handles both .mp3 and .m4a."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "ep_mp3.mp3").write_bytes(b"FAKE")
        (p / "ep_m4a.m4a").write_bytes(b"FAKE")

        model = _make_mock_model(["Een Nederlandse zin om te testen."])
        n = transcribe_dir(p, model=model)

        assert n == 2
        assert (p / "ep_mp3.txt").exists()
        assert (p / "ep_m4a.txt").exists()


def test_transcribe_dir_no_sidecar():
    """Works without a .json sidecar — no title prepended."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_audio(p)  # no sidecar

        model = _make_mock_model(["Alleen de tekst, geen titel."])
        n = transcribe_dir(p, model=model)

        assert n == 1
        content = (p / "episode_01.txt").read_text(encoding="utf-8")
        assert "Alleen de tekst" in content


def test_transcribe_dir_normalises_whitespace():
    """Collapses multiple spaces in transcript output."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_audio(p)

        model = _make_mock_model(["  Veel   spaties  hier  ."])
        n = transcribe_dir(p, model=model)

        assert n == 1
        content = (p / "episode_01.txt").read_text(encoding="utf-8")
        assert "  " not in content


def test_transcribe_dir_non_dutch_language_warning(caplog):
    """Logs a warning when Whisper detects unexpected language."""
    import logging
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_audio(p)

        model = MagicMock()
        info = SimpleNamespace(language="en")  # unexpected
        model.transcribe.return_value = ([_make_segment("Hello world.")], info)

        with caplog.at_level(logging.WARNING, logger="pipeline.asr.transcribe"):
            n = transcribe_dir(p, model=model)

        assert n == 1  # still transcribes despite wrong language
        assert "en" in caplog.text or "expected" in caplog.text


# ---------------------------------------------------------------------------
# run_asr — integration-style tests with mocked model loading
# ---------------------------------------------------------------------------

def test_run_asr_processes_known_sources():
    """run_asr walks source subdirs and calls transcribe_dir for each."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        uvn_dir = raw / "uvn_podcast"
        uvn_dir.mkdir()
        (uvn_dir / "ep1.mp3").write_bytes(b"FAKE")

        uvv_dir = raw / "uvv_podcast"
        uvv_dir.mkdir()
        (uvv_dir / "ep2.mp3").write_bytes(b"FAKE")

        # Other source — should be ignored
        other_dir = raw / "wikipedia"
        other_dir.mkdir()
        (other_dir / "page.mp3").write_bytes(b"FAKE")

        model = _make_mock_model(["Testje."])
        with patch("pipeline.asr.transcribe._load_model", return_value=model):
            counts = run_asr(raw, sources=["uvn_podcast", "uvv_podcast"])

        assert set(counts.keys()) == {"uvn_podcast", "uvv_podcast"}
        assert counts["uvn_podcast"] == 1
        assert counts["uvv_podcast"] == 1
        assert not (other_dir / "page.txt").exists()


def test_run_asr_respects_sources_filter():
    """run_asr only processes sources in the sources list."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        (raw / "uvn_podcast").mkdir()
        ((raw / "uvn_podcast") / "ep.mp3").write_bytes(b"FAKE")
        (raw / "uvv_podcast").mkdir()
        ((raw / "uvv_podcast") / "ep.mp3").write_bytes(b"FAKE")

        model = _make_mock_model(["Hallo wereld."])
        with patch("pipeline.asr.transcribe._load_model", return_value=model):
            counts = run_asr(raw, sources=["uvn_podcast"])

    assert "uvn_podcast" in counts
    assert "uvv_podcast" not in counts


def test_run_asr_empty_dir():
    """run_asr handles source dirs with no audio."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        (raw / "uvn_podcast").mkdir()
        # No .mp3 files

        model = _make_mock_model(["Dit wordt niet gebruikt."])
        with patch("pipeline.asr.transcribe._load_model", return_value=model):
            counts = run_asr(raw, sources=["uvn_podcast"])

    assert counts.get("uvn_podcast", 0) == 0


# ---------------------------------------------------------------------------
# Constants sanity check
# ---------------------------------------------------------------------------

def test_audio_exts_coverage():
    assert ".mp3" in _AUDIO_EXTS
    assert ".m4a" in _AUDIO_EXTS
    assert ".wav" in _AUDIO_EXTS
