"""
Tests for: fineweb2_nl, c5_dutch, dutch_pd parsers.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import (
    parse_fineweb2_nl_dir,
    parse_c5_dutch_dir,
    parse_dutch_pd_dir,
)

_BODY_NL = (
    "Nederland is een land in West-Europa met een rijke geschiedenis en cultuur. "
    "Het land staat bekend om zijn molens, tulpen en uitgestrekte polders. "
    "De Nederlandse economie behoort tot de grootste van Europa. "
    "Amsterdam is de hoofdstad en culturele metropool van het land. "
) * 3


# ---------------------------------------------------------------------------
# FineWeb-2 Dutch
# ---------------------------------------------------------------------------

def _fw_record(i: int, year: str = "2022") -> dict:
    return {
        "text": _BODY_NL,
        "url":  f"https://example.nl/artikel-{i}",
        "date": f"{year}-05-12T10:00:00Z",
        "id":   f"fw_{i}",
    }


def test_parse_fineweb2_nl_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_fw_record(i)) for i in range(3)]
        (p / "fineweb2_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_fineweb2_nl_dir(p))

    assert len(docs) == 3
    d = docs[0]
    assert d.meta.source == "fineweb2_nl"
    assert d.meta.license == "ODC-By"
    assert d.meta.domain == "web"
    assert d.meta.country == "NL"
    assert d.meta.publication_year == 2022
    assert "Nederland" in d.text


def test_parse_fineweb2_nl_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_fineweb2_nl_dir(Path(tmp))) == []


def test_parse_fineweb2_nl_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [
            json.dumps({"text": "Te kort.", "url": "x", "date": "2022", "id": "1"}),
            json.dumps(_fw_record(2)),
        ]
        (p / "fineweb2_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_fineweb2_nl_dir(p))

    assert len(docs) == 1


def test_parse_fineweb2_nl_multiple_shards():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            lines = [json.dumps(_fw_record(i * 10 + j)) for j in range(5)]
            (p / f"fineweb2_nl_shard_{i:06d}.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_fineweb2_nl_dir(p))

    assert len(docs) == 15


# ---------------------------------------------------------------------------
# C5 Dutch
# ---------------------------------------------------------------------------

_ABBR = {"CC-BY": "by", "CC-BY-SA": "by-sa", "CC0": "zero", "PD": "mark"}


def _c5_record(license_: str = "CC-BY", year: str = "2021") -> dict:
    return {
        "text":    _BODY_NL,
        "url":     "https://example.org/cc-page",
        "license": license_,
        "license_abbr": _ABBR.get(license_),
        "date":    year,
    }


def test_parse_c5_dutch_skips_records_without_c5_license():
    """Legacy raw records with a defaulted license and no license_abbr are not admitted."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        legacy = {"text": _BODY_NL, "url": "https://example.org/x", "license": "CC-BY", "date": "2021"}
        (p / "c5_dutch_shard_000000.jsonl").write_text(
            "\n".join([json.dumps(legacy), json.dumps(_c5_record("CC-BY-SA"))]), encoding="utf-8"
        )
        docs = list(parse_c5_dutch_dir(p))
    assert [d.meta.license for d in docs] == ["CC-BY-SA"]


def test_c5_license_from_abbr():
    from pipeline.acquire.c5_dutch import license_from_c5_abbr
    assert license_from_c5_abbr("by") == "CC-BY"
    assert license_from_c5_abbr("by-sa") == "CC-BY-SA"
    assert license_from_c5_abbr("zero") == "CC0"
    assert license_from_c5_abbr("mark") == "PD"
    for bad in ("by-nc", "by-nc-sa", "by-nc-nd", "by-nd", "cc-unknown", "certification", "", None):
        assert license_from_c5_abbr(bad) is None


def test_parse_c5_dutch_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_c5_record("CC-BY")), json.dumps(_c5_record("CC0"))]
        (p / "c5_dutch_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_c5_dutch_dir(p))

    assert len(docs) == 2
    licenses = {d.meta.license for d in docs}
    assert "CC-BY" in licenses
    assert "CC0" in licenses
    assert docs[0].meta.source == "c5_dutch"
    assert docs[0].meta.domain == "web"
    assert docs[0].meta.country == "NL"


def test_parse_c5_dutch_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_c5_dutch_dir(Path(tmp))) == []


def test_parse_c5_dutch_preserves_license():
    """Each document keeps its own CC license."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        records = [
            _c5_record("CC0"),
            _c5_record("CC-BY"),
            _c5_record("CC-BY-SA"),
        ]
        (p / "c5_dutch_shard_000000.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )
        docs = list(parse_c5_dutch_dir(p))

    assert len(docs) == 3
    licenses = [d.meta.license for d in docs]
    assert licenses == ["CC0", "CC-BY", "CC-BY-SA"]


def test_parse_c5_dutch_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [
            json.dumps({"text": "kort", "url": "", "license": "CC-BY", "date": ""}),
            json.dumps(_c5_record()),
        ]
        (p / "c5_dutch_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_c5_dutch_dir(p))

    assert len(docs) == 1


# ---------------------------------------------------------------------------
# PleIAs Dutch-PD
# ---------------------------------------------------------------------------

def _pd_record(source_dataset: str = "gutenberg", year: str = "1900") -> dict:
    return {
        "text":           _BODY_NL * 3,  # PD texts are longer
        "title":          "Een klassiek Nederlands werk",
        "date":           year,
        "source_dataset": source_dataset,
        "url":            "https://www.gutenberg.org/ebooks/12345",
    }


def test_parse_dutch_pd_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_pd_record("dbnl")), json.dumps(_pd_record("gutenberg"))]
        (p / "dutch_pd_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_dutch_pd_dir(p))

    assert len(docs) == 2
    d = docs[0]
    assert d.meta.source == "dutch_pd"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "literature"


def test_parse_dutch_pd_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_dutch_pd_dir(Path(tmp))) == []


def test_parse_dutch_pd_infers_news_domain():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_pd_record("delpher_krant"))]
        (p / "dutch_pd_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_dutch_pd_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.domain == "news"


def test_parse_dutch_pd_extracts_year():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_pd_record(year="1875-03-22"))]
        (p / "dutch_pd_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_dutch_pd_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 1875


def test_parse_dutch_pd_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [
            json.dumps({"text": "te kort", "title": "", "date": "", "source_dataset": "", "url": ""}),
            json.dumps(_pd_record()),
        ]
        (p / "dutch_pd_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_dutch_pd_dir(p))

    assert len(docs) == 1
