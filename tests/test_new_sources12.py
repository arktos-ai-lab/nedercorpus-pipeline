"""
Tests for: mc4_nl parser.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_mc4_nl_dir

_BODY_NL = (
    "Nederland is een land in West-Europa met een rijke geschiedenis en cultuur. "
    "Het land staat bekend om zijn molens, tulpen en uitgestrekte polders. "
    "De Nederlandse economie behoort tot de grootste van Europa. "
    "Amsterdam is de hoofdstad en culturele metropool van het land. "
) * 3


def _mc4_record(i: int, ts: str = "2020-06-15T00:00:00Z") -> dict:
    return {
        "text":      _BODY_NL,
        "url":       f"https://example.nl/artikel-{i}",
        "timestamp": ts,
    }


# ---------------------------------------------------------------------------
# Basic smoke test
# ---------------------------------------------------------------------------

def test_parse_mc4_nl_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_mc4_record(i)) for i in range(3)]
        (p / "mc4_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_mc4_nl_dir(p))

    assert len(docs) == 3
    d = docs[0]
    assert d.meta.source == "mc4_nl"
    assert d.meta.license == "ODC-By"
    assert d.meta.domain == "web"
    assert d.meta.country == "NL"
    assert "Nederland" in d.text


def test_parse_mc4_nl_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_mc4_nl_dir(Path(tmp))) == []


def test_parse_mc4_nl_extracts_year():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_mc4_record(1, ts="2019-11-03T12:00:00Z"))]
        (p / "mc4_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_mc4_nl_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 2019


def test_parse_mc4_nl_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [
            json.dumps({"text": "te kort", "url": "", "timestamp": "2020"}),
            json.dumps(_mc4_record(2)),
        ]
        (p / "mc4_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_mc4_nl_dir(p))

    assert len(docs) == 1


def test_parse_mc4_nl_multiple_shards():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(4):
            lines = [json.dumps(_mc4_record(i * 10 + j)) for j in range(5)]
            (p / f"mc4_nl_shard_{i:06d}.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_mc4_nl_dir(p))

    assert len(docs) == 20


def test_parse_mc4_nl_no_timestamp():
    """Records without a timestamp should still be parsed, with pub_year=None."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps({"text": _BODY_NL, "url": "https://x.nl/", "timestamp": ""})]
        (p / "mc4_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_mc4_nl_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year is None
