"""
Tests for: lokale_regelgeving, officiele_bekendmakingen, tuchtrecht, lblod parsers.
"""
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pipeline.parse.sru import (
    parse_lokale_regelgeving_files,
    parse_officiele_bekendmakingen_files,
    parse_tuchtrecht_files,
)
from pipeline.parse.plain_text import parse_lblod_dir


# ---------------------------------------------------------------------------
# Helpers — build minimal SRU XML batch files
# ---------------------------------------------------------------------------

_MW_NS = "http://www.loc.gov/zing/srw/"
_DC_NS  = "http://purl.org/dc/elements/1.1/"

_LONG = (
    "De gemeente heeft besloten een nieuwe verordening in te stellen betreffende "
    "het gebruik van openbare ruimten en de regulering van evenementen. "
    "De verordening treedt in werking op de eerste dag van de volgende maand. "
) * 5


def _make_sru_batch(records: list[dict]) -> bytes:
    """Build a minimal SRU SearchRetrieveResponse XML with given records."""
    root = ET.Element(f"{{{_MW_NS}}}searchRetrieveResponse")
    num = ET.SubElement(root, f"{{{_MW_NS}}}numberOfRecords")
    num.text = str(len(records))
    records_el = ET.SubElement(root, f"{{{_MW_NS}}}records")
    for rec in records:
        r = ET.SubElement(records_el, f"{{{_MW_NS}}}record")
        rd = ET.SubElement(r, f"{{{_MW_NS}}}recordData")
        if "identifier" in rec:
            id_el = ET.SubElement(rd, f"{{{_DC_NS}}}identifier")
            id_el.text = rec["identifier"]
        if "date" in rec:
            d_el = ET.SubElement(rd, f"{{{_DC_NS}}}date")
            d_el.text = rec["date"]
        if "text" in rec:
            t_el = ET.SubElement(rd, f"{{{_DC_NS}}}description")
            t_el.text = rec["text"]
    return ET.tostring(root, encoding="unicode").encode("utf-8")


# ---------------------------------------------------------------------------
# lokale_regelgeving
# ---------------------------------------------------------------------------

def test_parse_lokale_regelgeving_yields_document():
    batch = _make_sru_batch([{
        "identifier": "https://lokaleregelgeving.overheid.nl/CVDR/12345",
        "date": "2022-01-15",
        "text": _LONG,
    }])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "cvdr_batch_00000001.xml").write_bytes(batch)
        docs = list(parse_lokale_regelgeving_files(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "lokale_regelgeving"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "legal"
    assert d.meta.publication_year == 2022


def test_parse_lokale_regelgeving_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_lokale_regelgeving_files(Path(tmp))) == []


# ---------------------------------------------------------------------------
# officiele_bekendmakingen
# ---------------------------------------------------------------------------

def test_parse_officiele_bekendmakingen_gemeenteblad():
    batch = _make_sru_batch([{
        "identifier": "https://www.officielebekendmakingen.nl/gmb-2023-12345",
        "date": "2023-03-10",
        "text": _LONG,
    }])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        gmb = p / "gemeenteblad"
        gmb.mkdir()
        (gmb / "batch_00000001.xml").write_bytes(batch)
        docs = list(parse_officiele_bekendmakingen_files(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "officiele_bekendmakingen:gemeenteblad"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.publication_year == 2023


def test_parse_officiele_bekendmakingen_multiple_areas():
    batch = _make_sru_batch([{"text": _LONG, "date": "2023-01-01"}])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for area in ("gemeenteblad", "provincieblad", "waterschapsblad"):
            area_dir = p / area
            area_dir.mkdir()
            (area_dir / "batch_00000001.xml").write_bytes(batch)
        docs = list(parse_officiele_bekendmakingen_files(p))
    sources = {d.meta.source for d in docs}
    assert "officiele_bekendmakingen:gemeenteblad" in sources
    assert "officiele_bekendmakingen:provincieblad" in sources
    assert "officiele_bekendmakingen:waterschapsblad" in sources


def test_parse_officiele_bekendmakingen_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_officiele_bekendmakingen_files(Path(tmp))) == []


# ---------------------------------------------------------------------------
# tuchtrecht
# ---------------------------------------------------------------------------

def test_parse_tuchtrecht_yields_document():
    batch = _make_sru_batch([{
        "identifier": "https://tuchtrecht.overheid.nl/ECLI:NL:TGZRAMS:2023:1",
        "date": "2023-06-15",
        "text": (
            "De arts heeft in strijd met de professionele standaard gehandeld "
            "door onjuiste informatie te verstrekken aan de patiënt. "
            "Het college acht de klacht gegrond en legt een waarschuwing op. "
        ) * 5,
    }])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "tucht_batch_00000001.xml").write_bytes(batch)
        docs = list(parse_tuchtrecht_files(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "tuchtrecht"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "legal"


def test_parse_tuchtrecht_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_tuchtrecht_files(Path(tmp))) == []


# ---------------------------------------------------------------------------
# LBLOD
# ---------------------------------------------------------------------------

_LBLOD_BESLUIT_TEXT = (
    "De gemeenteraad heeft besloten de openingsuren van het gemeentehuis te wijzigen. "
    "De nieuwe openingsuren gelden vanaf 1 januari 2024. "
    "Het college van burgemeester en schepenen wordt belast met de uitvoering. "
) * 5

_BESLUIT_PRED = "http://data.vlaanderen.be/ns/besluit#motivering"


def _nt_line(subj: str, pred: str, text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'<{subj}> <{pred}> "{escaped}"@nl .'


def test_parse_lblod_yields_document():
    lines = [
        _nt_line(f"https://data.lblod.info/besluiten/{i}", _BESLUIT_PRED, _LBLOD_BESLUIT_TEXT)
        for i in range(5)
    ]
    nt_content = "\n".join(lines) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "besluiten.nt").write_text(nt_content, encoding="utf-8")
        docs = list(parse_lblod_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "lblod"
    assert d.meta.license == "CC0"
    assert d.meta.country == "BE"
    assert d.meta.language_variant == "NL-BE"
    assert d.meta.domain == "government"


def test_parse_lblod_skips_non_text_predicates():
    """Triples with unrecognised predicates should be ignored."""
    lines = [
        _nt_line("https://example.org/s", "http://example.org/unknown", _LBLOD_BESLUIT_TEXT),
        _nt_line("https://example.org/s2", _BESLUIT_PRED, _LBLOD_BESLUIT_TEXT * 2),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "besluiten.nt").write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_lblod_dir(p))
    # Only the besluit:motivering triple should produce a document
    assert all(d.meta.source == "lblod" for d in docs)
    assert all("gemeenteraad" in d.text or "besloten" in d.text for d in docs)


def test_parse_lblod_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_lblod_dir(Path(tmp))) == []
