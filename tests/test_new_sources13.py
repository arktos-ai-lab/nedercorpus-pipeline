"""
Tests for: rce, rivm parsers.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_rce_dir, parse_rivm_dir

_OMSCHRIJVING = (
    "Dit rijksmonument is een bijzonder voorbeeld van de Nederlandse "
    "bouwkunst uit de zeventiende eeuw. Het gebouw is opgetrokken uit "
    "rode baksteen en heeft een karakteristieke trapgevel. De historische "
    "waarde van dit pand is groot voor de regionale architectuurgeschiedenis."
) * 2

_ABSTRACT = (
    "Dit rapport beschrijft de resultaten van een onderzoek naar de "
    "luchtkwaliteit in Nederland. De metingen zijn uitgevoerd op meerdere "
    "locaties verspreid over het land. De resultaten tonen aan dat de "
    "concentraties fijnstof op sommige locaties de norm overschrijden."
) * 2


# ---------------------------------------------------------------------------
# RCE
# ---------------------------------------------------------------------------

def _rce_record(i: int) -> dict:
    return {
        "id":           f"https://ceo.linkeddata.cultureelerfgoed.nl/objects/{i:05d}",
        "naam":         f"Rijksmonument {i}",
        "omschrijving": _OMSCHRIJVING,
        "acquisition_date": "2026-01-01",
    }


def test_parse_rce_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_rce_record(i)) for i in range(3)]
        (p / "rce_batch_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_rce_dir(p))

    assert len(docs) == 3
    d = docs[0]
    assert d.meta.source == "rce"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "cultural_heritage"
    assert d.meta.country == "NL"
    assert "Rijksmonument" in d.text
    assert _OMSCHRIJVING[:30] in d.text


def test_parse_rce_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_rce_dir(Path(tmp))) == []


def test_parse_rce_skips_empty_omschrijving():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        records = [
            {"id": "x1", "naam": "Gebouw", "omschrijving": "", "acquisition_date": "2026-01-01"},
            _rce_record(2),
        ]
        (p / "rce_batch_000000.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )
        docs = list(parse_rce_dir(p))

    assert len(docs) == 1


def test_parse_rce_uses_http_id_as_url():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        rec = _rce_record(42)
        (p / "rce_batch_000000.jsonl").write_text(json.dumps(rec), encoding="utf-8")
        docs = list(parse_rce_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.source_url.startswith("https://")


def test_parse_rce_multiple_batches():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            lines = [json.dumps(_rce_record(i * 10 + j)) for j in range(4)]
            (p / f"rce_batch_{i:06d}.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_rce_dir(p))

    assert len(docs) == 12


# ---------------------------------------------------------------------------
# RIVM
# ---------------------------------------------------------------------------

def _rivm_record(i: int, lang: str = "nl", year: int = 2022) -> dict:
    return {
        "title":       f"RIVM rapport {i}: Luchtkwaliteit in Nederland",
        "description": _ABSTRACT,
        "language":    lang,
        "date":        f"{year}-06-01",
        "identifier":  f"https://rivm.openrepository.com/handle/10029/{1000 + i}",
        "pub_year":    year,
    }


def test_parse_rivm_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_rivm_record(i)) for i in range(3)]
        (p / "rivm_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_rivm_dir(p))

    assert len(docs) == 3
    d = docs[0]
    assert d.meta.source == "rivm"
    assert d.meta.license == "PD"
    assert d.meta.domain == "science"
    assert d.meta.country == "NL"
    assert "RIVM rapport" in d.text


def test_parse_rivm_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_rivm_dir(Path(tmp))) == []


def test_parse_rivm_extracts_year():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_rivm_record(1, year=2019))]
        (p / "rivm_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_rivm_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 2019


def test_parse_rivm_skips_empty():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        records = [
            {"title": "", "description": "", "language": "nl", "date": "", "identifier": "", "pub_year": None},
            _rivm_record(2),
        ]
        (p / "rivm_shard_000000.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )
        docs = list(parse_rivm_dir(p))

    assert len(docs) == 1


def test_parse_rivm_uses_identifier_as_url():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "rivm_shard_000000.jsonl").write_text(json.dumps(_rivm_record(7)), encoding="utf-8")
        docs = list(parse_rivm_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.source_url.startswith("https://")


def test_parse_rivm_multiple_shards():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            lines = [json.dumps(_rivm_record(i * 10 + j)) for j in range(5)]
            (p / f"rivm_shard_{i:06d}.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_rivm_dir(p))

    assert len(docs) == 15
