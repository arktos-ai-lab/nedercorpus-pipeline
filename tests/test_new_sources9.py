"""
Tests for: openalex parser.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_openalex_dir


_ABSTRACT = (
    "Dit artikel onderzoekt de impact van digitalisering op de Nederlandse arbeidsmarkt. "
    "We analyseren data van het CBS en concluderen dat automatisering leidt tot significante "
    "verschuivingen in de vraag naar arbeid, met name in de maakindustrie. "
    "Onze resultaten suggereren dat beleid gericht op om- en bijscholing cruciaal is. "
)


def _make_page(path: Path, works: list[dict]) -> None:
    lines = [json.dumps(w, ensure_ascii=False) for w in works]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_parse_openalex_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_page(p / "openalex_page_000000.jsonl", [
            {
                "id": "https://openalex.org/W12345",
                "title": "Digitalisering en de arbeidsmarkt",
                "abstract": _ABSTRACT,
                "publication_year": 2021,
                "doi": "https://doi.org/10.1000/test",
            }
        ])
        docs = list(parse_openalex_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "openalex"
    assert d.meta.license == "CC0"
    assert d.meta.country == "NL"
    assert d.meta.domain == "academic"
    assert d.meta.publication_year == 2021
    assert "arbeidsmarkt" in d.text.lower() or "digitalisering" in d.text.lower()


def test_parse_openalex_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_openalex_dir(Path(tmp))) == []


def test_parse_openalex_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_page(p / "openalex_page_000000.jsonl", [
            {"id": "W1", "title": "Kort", "abstract": "Te kort.", "publication_year": 2020},
            {
                "id": "W2",
                "title": "Digitalisering in Nederland",
                "abstract": _ABSTRACT,
                "publication_year": 2019,
            },
        ])
        docs = list(parse_openalex_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 2019


def test_parse_openalex_multiple_pages():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            _make_page(p / f"openalex_page_{i:06d}.jsonl", [
                {
                    "id": f"W{i}00",
                    "title": f"Wetenschappelijk artikel {i}",
                    "abstract": _ABSTRACT,
                    "publication_year": 2020 + i,
                }
            ])
        docs = list(parse_openalex_dir(p))

    assert len(docs) == 3
    years = {d.meta.publication_year for d in docs}
    assert years == {2020, 2021, 2022}


def test_parse_openalex_title_only():
    """Documents with title but no abstract still yield if title is long enough."""
    long_title = "Wetenschappelijke studie naar de effecten van klimaatverandering op de Nederlandse kustlijn en deltaregio" * 1
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _make_page(p / "openalex_page_000000.jsonl", [
            {"id": "W999", "title": long_title, "abstract": "", "publication_year": 2023},
        ])
        docs = list(parse_openalex_dir(p))

    # Long title (>100 chars) should yield a document
    if len(long_title) >= 100:
        assert len(docs) == 1
        assert docs[0].meta.source == "openalex"
