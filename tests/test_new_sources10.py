"""
Tests for: wikidata_nl parser.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_wikidata_nl_dir


# ---------------------------------------------------------------------------
# Wikidata Dutch descriptions
# ---------------------------------------------------------------------------

_WIKIDATA_RECORDS = [
    {"id": "Q64", "label": "Berlijn", "description": "hoofdstad en deelstaat van Duitsland"},
    {"id": "Q142", "label": "Frankrijk", "description": "land in West-Europa"},
    {"id": "Q183", "label": "Duitsland", "description": "land in Midden-Europa"},
    {"id": "Q35", "label": "Denemarken", "description": "land in Noord-Europa"},
    {"id": "Q55", "label": "Nederland", "description": "land in West-Europa"},
]


def _make_wikidata_jsonl(path: Path, records: list[dict]) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_parse_wikidata_nl_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        # Create enough records to trigger a flush (200 per doc, so use 5 and check remainder)
        records = _WIKIDATA_RECORDS * 10  # 50 records
        _make_wikidata_jsonl(p / "wikidata_nl_000000.jsonl", records)
        docs = list(parse_wikidata_nl_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "wikidata_nl"
    assert d.meta.license == "CC0"
    assert d.meta.domain == "encyclopedic"
    assert d.meta.country == "NL"
    assert "Nederland" in d.text or "Berlijn" in d.text


def test_parse_wikidata_nl_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_wikidata_nl_dir(Path(tmp))) == []


def test_parse_wikidata_nl_batches_entities():
    """Parser should batch 200 entities per Document."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        # 250 records → should produce 2 documents (200 + 50)
        records = [
            {"id": f"Q{i}", "label": f"Entiteit {i}", "description": f"een beschrijving van entiteit {i}"}
            for i in range(250)
        ]
        _make_wikidata_jsonl(p / "wikidata_nl_000000.jsonl", records)
        docs = list(parse_wikidata_nl_dir(p))

    assert len(docs) == 2
    # First doc: 200 entities
    assert docs[0].text.count("\n") == 199


def test_parse_wikidata_nl_multiple_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            records = [
                {"id": f"Q{i*10+j}", "label": f"Land {i*10+j}", "description": "een Europees land"}
                for j in range(10)
            ]
            _make_wikidata_jsonl(p / f"wikidata_nl_{i:06d}.jsonl", records)
        docs = list(parse_wikidata_nl_dir(p))

    # 3 files × 10 records each → batch flushed per file → 3 docs
    assert len(docs) == 3
    all_text = " ".join(d.text for d in docs)
    assert "Land" in all_text
    assert all(d.meta.source == "wikidata_nl" for d in docs)
