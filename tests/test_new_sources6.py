"""
Tests for: DCEP parser, OPUS-DGT/EUbookshop (via opus_extra),
and UD Frisian-Dutch FAME (via ud_dutch).
"""
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_dcep_dir, parse_opus_extra_dir, parse_ud_dutch_dir


_LONG_TEXT = (
    "De Europese Commissie heeft een voorstel ingediend voor een nieuwe verordening "
    "betreffende de bescherming van persoonsgegevens in de Europese Unie. "
    "Het Europees Parlement heeft het voorstel in eerste lezing goedgekeurd met wijzigingen. "
    "De Raad van de Europese Unie heeft zijn standpunt bepaald en onderhandelingen zijn gestart. "
) * 30


# ---------------------------------------------------------------------------
# DCEP parser
# ---------------------------------------------------------------------------

def test_parse_dcep_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "dcep_nl.txt").write_text(_LONG_TEXT, encoding="utf-8")
        docs = list(parse_dcep_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "dcep"
    assert d.meta.license == "CC-BY"
    assert d.meta.language_variant == "NL"
    assert d.meta.domain == "parliamentary"
    assert "Europese" in d.text or "Parlement" in d.text


def test_parse_dcep_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_dcep_dir(Path(tmp))) == []


def test_parse_dcep_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "tiny.txt").write_text("Kort.", encoding="utf-8")
        assert list(parse_dcep_dir(p)) == []


def test_parse_dcep_recursive():
    """DCEP is extracted as a directory tree; parser must recurse."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        subdir = p / "2009" / "nl"
        subdir.mkdir(parents=True)
        (subdir / "press_release.txt").write_text(_LONG_TEXT, encoding="utf-8")
        docs = list(parse_dcep_dir(p))
    assert len(docs) >= 1
    assert docs[0].meta.source == "dcep"


# ---------------------------------------------------------------------------
# OPUS DGT / EUbookshop — via opus_extra parser
# ---------------------------------------------------------------------------

def test_parse_opus_extra_dgt():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        dgt_dir = p / "dgt"
        dgt_dir.mkdir()
        (dgt_dir / "nl.txt").write_text(
            "\n".join(["De Commissie stelt voor dat lidstaten maatregelen nemen."] * 600),
            encoding="utf-8",
        )
        docs = list(parse_opus_extra_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "opus_extra"
    assert d.meta.license == "PD"
    assert d.meta.domain == "legal"


def test_parse_opus_extra_eubookshop():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        eb_dir = p / "eubookshop"
        eb_dir.mkdir()
        (eb_dir / "nl.txt").write_text(
            "\n".join(["De Europese Unie publiceert regelmatig rapporten over haar beleid."] * 600),
            encoding="utf-8",
        )
        docs = list(parse_opus_extra_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "opus_extra"
    assert d.meta.license == "PD"


# ---------------------------------------------------------------------------
# UD Frisian-Dutch FAME — via ud_dutch parser
# ---------------------------------------------------------------------------

_FAME_SENTENCES = "\n".join([
    "Hy sei dat hy moarn komme soe.",
    "Ze zei dat ze morgen zou komen.",
    "De bern boartsje bûten yn 'e tún.",
    "De kinderen spelen buiten in de tuin.",
] * 30)


def test_parse_ud_dutch_fame():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        fame_dir = p / "frisian_dutch_fame"
        fame_dir.mkdir()
        (fame_dir / "sentences.txt").write_text(_FAME_SENTENCES, encoding="utf-8")
        docs = list(parse_ud_dutch_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "ud_dutch"
    assert d.meta.license == "CC-BY-SA"
    assert d.meta.language_variant == "FY"
    assert d.meta.country == "NL"
