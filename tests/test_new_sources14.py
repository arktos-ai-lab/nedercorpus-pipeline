"""
Tests for: grondwettelijk_hof parser.
"""
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_grondwettelijk_hof_dir

# Realistic Dutch legal decision text (repeated to exceed 100 chars easily)
_DECISION_TEXT = (
    "Het Grondwettelijk Hof, samengesteld uit de voorzitters F. Daoût en L. Lavrysen, "
    "en de rechters J.-P. Snappe, J. Moerman, E. Derycke, T. Merckx-Van Goey, P. Nihoul, "
    "F. Stranart, R. Leysen, J. Ketelaars, S. de Bethune en E. Vandendriessche, "
    "bijgestaan door de griffier F. Meersschaut, "
    "uitspraak doende zoals bepaald in artikel 65 van de bijzondere wet van 6 januari 1989 "
    "op het Grondwettelijk Hof, wijst na beraad het volgende arrest. "
) * 4


# ---------------------------------------------------------------------------
# test 1 – basic smoke test
# ---------------------------------------------------------------------------

def test_parse_grondwettelijk_hof_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "ghcc_2022_001.txt").write_text(_DECISION_TEXT, encoding="utf-8")
        docs = list(parse_grondwettelijk_hof_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "grondwettelijk_hof"
    assert d.meta.license == "PD"
    assert d.meta.domain == "legal"
    assert d.meta.country == "BE"
    assert d.meta.language_variant == "NL-BE"
    assert "Grondwettelijk Hof" in d.text


# ---------------------------------------------------------------------------
# test 2 – empty directory returns no documents
# ---------------------------------------------------------------------------

def test_parse_grondwettelijk_hof_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_grondwettelijk_hof_dir(Path(tmp))) == []


# ---------------------------------------------------------------------------
# test 3 – year is extracted correctly from filename
# ---------------------------------------------------------------------------

def test_parse_grondwettelijk_hof_extracts_year():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "ghcc_1999_042.txt").write_text(_DECISION_TEXT, encoding="utf-8")
        docs = list(parse_grondwettelijk_hof_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 1999


# ---------------------------------------------------------------------------
# test 4 – documents shorter than 100 chars are skipped
# ---------------------------------------------------------------------------

def test_parse_grondwettelijk_hof_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        # Too short
        (p / "ghcc_2020_001.txt").write_text("Kort.", encoding="utf-8")
        # Long enough
        (p / "ghcc_2020_002.txt").write_text(_DECISION_TEXT, encoding="utf-8")
        docs = list(parse_grondwettelijk_hof_dir(p))

    assert len(docs) == 1
    assert docs[0].meta.publication_year == 2020


# ---------------------------------------------------------------------------
# test 5 – source_url is constructed correctly
# ---------------------------------------------------------------------------

def test_parse_grondwettelijk_hof_constructs_url():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "ghcc_2015_007.txt").write_text(_DECISION_TEXT, encoding="utf-8")
        docs = list(parse_grondwettelijk_hof_dir(p))

    assert len(docs) == 1
    expected_url = "https://nl.const-court.be/public/n/2015/2015-007n.pdf"
    assert docs[0].meta.source_url == expected_url


# ---------------------------------------------------------------------------
# test 6 – multiple files are all parsed
# ---------------------------------------------------------------------------

def test_parse_grondwettelijk_hof_multiple_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for year in (2021, 2022, 2023):
            for num in range(1, 4):
                fname = f"ghcc_{year}_{num:03d}.txt"
                (p / fname).write_text(_DECISION_TEXT, encoding="utf-8")
        docs = list(parse_grondwettelijk_hof_dir(p))

    assert len(docs) == 9
    years = {d.meta.publication_year for d in docs}
    assert years == {2021, 2022, 2023}
