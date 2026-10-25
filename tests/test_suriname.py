"""Tests for the Suriname acquirer module."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from pipeline.acquire.suriname import (
    download_staatsblad,
    download_dna,
    download_rechtspraak_sr,
    download_national_archives,
    download,
)


def test_dry_run_staatsblad():
    with tempfile.TemporaryDirectory() as tmp:
        download_staatsblad(Path(tmp), year_from=2020, year_to=2020, dry_run=True)
        # dry-run should not create any year subdirectories
        assert not (Path(tmp) / "staatsblad" / "2020").exists()


def test_dry_run_dna():
    with tempfile.TemporaryDirectory() as tmp:
        download_dna(Path(tmp), dry_run=True)
        # dry-run: index page directories created but no fetches
        dna_dir = Path(tmp) / "dna"
        assert not any(dna_dir.rglob("index.html")) if dna_dir.exists() else True


def test_dry_run_rechtspraak():
    with tempfile.TemporaryDirectory() as tmp:
        download_rechtspraak_sr(Path(tmp), dry_run=True)
        dest = Path(tmp) / "rechtspraak_sr"
        # dry-run skips fetch entirely
        assert not (dest / "index.html").exists()


def test_dry_run_national_archives():
    with tempfile.TemporaryDirectory() as tmp:
        download_national_archives(Path(tmp), dry_run=True)
        dest = Path(tmp) / "national_archives_sr"
        assert dest.exists()
        # No JSON files should have been written
        assert list(dest.glob("*.json")) == []


def test_dry_run_combined():
    """Combined download with dry-run should not raise."""
    with tempfile.TemporaryDirectory() as tmp:
        download(Path(tmp), sources=["staatsblad", "dna", "rechtspraak", "archives"], dry_run=True)


def test_log_entry_written_on_fetch():
    """Acquisition log should be written when a fetch succeeds."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)

        fake_html = b"<html><body>Staatsblad 2023</body></html>"

        with patch("pipeline.acquire.suriname._fetch", return_value=fake_html):
            download_staatsblad(out, year_from=2023, year_to=2023, dry_run=False)

        log_path = out / "acquisition_log.jsonl"
        assert log_path.exists()
        entries = [json.loads(l) for l in log_path.read_text().splitlines()]
        assert any("staatsblad" in e["source"] for e in entries)
        assert all(e["license"] == "PD" for e in entries)
        assert all(e["country"] == "SR" for e in entries)


def test_schema_accepts_sr():
    """DocumentMeta should accept SR as country and language_variant."""
    from pipeline.schema import DocumentMeta
    from pipeline.document import make_id
    meta = DocumentMeta(
        id=make_id("suriname_staatsblad"),
        source="suriname_staatsblad",
        source_url="https://gov.sr/staatsblad/2023/sb2023_001.pdf",
        license="PD",
        publication_year=2023,
        country="SR",
        domain="legal",
        language_variant="SR",
    )
    d = meta.to_dict()
    assert d["country"] == "SR"
    assert d["language_variant"] == "SR"
