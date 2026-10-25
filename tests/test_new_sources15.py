"""
Tests for: openaire_nl parser.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_openaire_nl_dir

_ABSTRACT = (
    "Dit artikel beschrijft de resultaten van een wetenschappelijk onderzoek naar "
    "duurzame energiebronnen in Nederland. De studie analyseert de effectiviteit van "
    "zonne-energie en windenergie bij het verminderen van CO2-uitstoot. "
    "De conclusies tonen significante verbeteringen in energieopwekking."
) * 3


def _oa_record(i, year=2022, rights="CC-BY"):
    return {
        "title": f"Onderzoek {i}: Duurzame energie in de Nederlandse context",
        "abstract": _ABSTRACT,
        "language": "nl",
        "date": f"{year}-06-01",
        "identifier": f"https://hdl.handle.net/1887/{10000 + i}",
        "pub_year": year,
        "rights": rights,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_parse_openaire_nl_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_oa_record(i)) for i in range(3)]
        (p / "openaire_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_openaire_nl_dir(p))

    assert len(docs) == 3
    d = docs[0]
    assert d.meta.source == "openaire_nl"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "science"
    assert d.meta.country == "NL"
    assert "Onderzoek 0" in d.text
    assert _ABSTRACT[:30] in d.text


def test_parse_openaire_nl_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_openaire_nl_dir(Path(tmp))) == []


def test_parse_openaire_nl_extracts_year():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_oa_record(1, year=2019))]
        (p / "openaire_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_openaire_nl_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 2019


def test_parse_openaire_nl_skips_nc():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        lines = [json.dumps(_oa_record(1, rights="CC-BY-NC"))]
        (p / "openaire_nl_shard_000000.jsonl").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_openaire_nl_dir(p))

    assert len(docs) == 0


def test_parse_openaire_nl_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        record = {
            "title": "",
            "abstract": "",
            "language": "nl",
            "date": "2022-01-01",
            "identifier": "https://hdl.handle.net/1887/99999",
            "pub_year": 2022,
            "rights": "CC-BY",
        }
        (p / "openaire_nl_shard_000000.jsonl").write_text(
            json.dumps(record), encoding="utf-8"
        )
        docs = list(parse_openaire_nl_dir(p))

    assert len(docs) == 0


def test_parse_openaire_nl_multiple_shards():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            lines = [json.dumps(_oa_record(i * 10 + j)) for j in range(4)]
            (p / f"openaire_nl_shard_{i:06d}.jsonl").write_text(
                "\n".join(lines), encoding="utf-8"
            )
        docs = list(parse_openaire_nl_dir(p))

    assert len(docs) == 12
