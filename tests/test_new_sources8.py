"""
Tests for: tractatenblad, advisory_bodies parsers.
"""
import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pipeline.parse.sru import parse_tractatenblad_files
from pipeline.parse.plain_text import (
    parse_advisory_bodies_dir, parse_oscar_dir, parse_hplt_dir,
    parse_rijksmuseum_dir,
)


# ---------------------------------------------------------------------------
# Helpers — build minimal SRU XML batch files
# ---------------------------------------------------------------------------

_MW_NS = "http://www.loc.gov/zing/srw/"
_DC_NS  = "http://purl.org/dc/elements/1.1/"

_LONG = (
    "Nederland en de partijstaten zijn overeengekomen de volgende bepalingen "
    "in het verdrag op te nemen betreffende de samenwerking op het gebied van "
    "wetenschap, technologie en economische betrekkingen. "
) * 5


def _make_sru_batch(records: list[dict]) -> bytes:
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
# tractatenblad
# ---------------------------------------------------------------------------

def test_parse_tractatenblad_yields_document():
    batch = _make_sru_batch([{
        "identifier": "https://www.officielebekendmakingen.nl/trb-2022-12",
        "date": "2022-05-01",
        "text": _LONG,
    }])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "trb_batch_00000001.xml").write_bytes(batch)
        docs = list(parse_tractatenblad_files(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "tractatenblad"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "legal"
    assert d.meta.publication_year == 2022
    assert "verdrag" in d.text.lower() or "partijstaten" in d.text.lower()


def test_parse_tractatenblad_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_tractatenblad_files(Path(tmp))) == []


def test_parse_tractatenblad_multiple_records():
    batch = _make_sru_batch([
        {"text": _LONG, "date": "2020-01-01"},
        {"text": _LONG * 2, "date": "2021-06-15"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "trb_batch_00000001.xml").write_bytes(batch)
        docs = list(parse_tractatenblad_files(p))
    assert len(docs) >= 2


# ---------------------------------------------------------------------------
# advisory_bodies
# ---------------------------------------------------------------------------

_MINIMAL_PDF_TEXT = (
    "De Wetenschappelijke Raad voor het Regeringsbeleid adviseert de regering "
    "over structurele vraagstukken die van belang zijn voor de samenleving. "
    "In dit rapport worden de uitdagingen op het gebied van digitalisering "
    "en de impact op de arbeidsmarkt besproken. "
) * 6


def _make_fake_pdf(path: Path, text: str) -> None:
    """
    Write a minimal PDF-like file. Since pdf_text.extract_text uses pdfplumber,
    we can't easily make a real PDF in tests. Instead, write a .txt file
    alongside the pdf and monkey-patch the extractor in tests.
    """
    # Write text sidecar that the test mock uses
    path.write_bytes(b"%PDF-1.4 fake")
    path.with_suffix(".txt.extracted").write_text(text, encoding="utf-8")


def test_parse_advisory_bodies_wrr():
    """Advisory bodies parser yields documents from wrr/ subdirectory."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        wrr_dir = p / "wrr"
        wrr_dir.mkdir()

        (wrr_dir / "wrr_records.jsonl").write_text(
            json.dumps({
                "source_url": "https://www.wrr.nl/publicaties/rapporten/2022/01/01/test",
                "license": "CC-BY",
                "country": "NL",
                "domain": "government",
                "acquisition_date": "2026-01-01",
                "text": _MINIMAL_PDF_TEXT,
                "title": "WRR Rapport 2022",
            }) + "\n",
            encoding="utf-8",
        )

        docs = list(parse_advisory_bodies_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "advisory_bodies:wrr"
    assert d.meta.license == "CC-BY"
    assert d.meta.country == "NL"
    assert d.meta.domain == "government"
    assert d.meta.language_variant == "NL"


def test_parse_advisory_bodies_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_advisory_bodies_dir(Path(tmp)))
    assert docs == []


def test_parse_advisory_bodies_multiple_bodies():
    """Parser handles wrr, ser, cpb, pbl subdirectories."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for body in ("wrr", "ser", "cpb", "pbl"):
            body_dir = p / body
            body_dir.mkdir()
            (body_dir / f"{body}_records.jsonl").write_text(
                json.dumps({
                    "source_url": f"https://www.{body}.nl/test",
                    "text": _MINIMAL_PDF_TEXT,
                    "title": f"{body.upper()} test",
                }) + "\n",
                encoding="utf-8",
            )

        docs = list(parse_advisory_bodies_dir(p))

    sources = {d.meta.source for d in docs}
    assert "advisory_bodies:wrr" in sources
    assert "advisory_bodies:ser" in sources
    assert "advisory_bodies:cpb" in sources
    assert "advisory_bodies:pbl" in sources


# ---------------------------------------------------------------------------
# OSCAR (Common Crawl Dutch subset, CC0)
# ---------------------------------------------------------------------------

_OSCAR_TEXT = (
    "Amsterdam is de hoofdstad van Nederland en de meest bevolkte stad van het land. "
    "De stad is gelegen aan het IJ, een voormalige Zuiderzeebaai. "
    "Amsterdam staat bekend om zijn grachtengordel, rijksmuseum en culturele diversiteit. "
) * 4


def test_parse_oscar_dir_yields_document():
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        shard = p / "oscar_nl_shard_000000.jsonl"
        lines = [
            _json.dumps({"text": _OSCAR_TEXT, "meta": {"url": "https://example.nl/article-2022", "quality_warnings": []}}),
            _json.dumps({"text": _OSCAR_TEXT * 2, "meta": {"url": "https://nieuwsblad.nl/2021/artikel"}}),
        ]
        shard.write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_oscar_dir(p))

    assert len(docs) >= 2
    d = docs[0]
    assert d.meta.source == "oscar"
    assert d.meta.license == "CC0"
    assert d.meta.country == "NL"
    assert d.meta.domain == "web"
    assert d.meta.language_variant == "NL"
    assert d.meta.publication_year == 2022


def test_parse_oscar_dir_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_oscar_dir(Path(tmp))) == []


def test_parse_oscar_dir_skips_short_texts():
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        shard = p / "oscar_nl_shard_000000.jsonl"
        lines = [
            _json.dumps({"text": "Te kort.", "meta": {}}),
            _json.dumps({"text": _OSCAR_TEXT, "meta": {"url": "https://example.nl/"}}),
        ]
        shard.write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_oscar_dir(p))

    assert len(docs) == 1


# ---------------------------------------------------------------------------
# HPLT Dutch (Internet Archive corpus, CC0)
# ---------------------------------------------------------------------------

_HPLT_TEXT = (
    "Rotterdam is de tweede stad van Nederland en heeft de grootste haven van Europa. "
    "De haven verwerkt jaarlijks miljoenen tonnen goederen en is van groot economisch belang. "
    "De stad is volledig herbouwd na de Tweede Wereldoorlog en staat bekend om zijn moderne architectuur. "
) * 4


def test_parse_hplt_dir_yields_document():
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        shard = p / "hplt_nl_0001.jsonl"
        lines = [
            _json.dumps({"id": "1", "text": _HPLT_TEXT, "url": "https://example.nl/2022/artikel", "document_lang": "nl"}),
            _json.dumps({"id": "2", "text": _HPLT_TEXT * 2, "url": "https://blog.nl/post", "document_lang": "nl"}),
        ]
        shard.write_text("\n".join(lines), encoding="utf-8")
        docs = list(parse_hplt_dir(p))

    assert len(docs) >= 2
    d = docs[0]
    assert d.meta.source == "hplt"
    assert d.meta.license == "CC0"
    assert d.meta.country == "NL"
    assert d.meta.domain == "web"
    assert d.meta.language_variant == "NL"
    assert d.meta.publication_year == 2022


def test_parse_hplt_dir_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_hplt_dir(Path(tmp))) == []


# ---------------------------------------------------------------------------
# Rijksmuseum collection metadata (CC0)
# ---------------------------------------------------------------------------

def test_parse_rijksmuseum_dir_yields_document():
    import json as _json
    page_data = {
        "artObjects": [
            {
                "objectNumber": "SK-C-5",
                "title": "De Nachtwacht",
                "longTitle": "De Nachtwacht, Rembrandt van Rijn, 1642",
                "subTitle": "h 379,5cm × b 453,5cm × g 337kg",
                "plaqueDescriptionDutch": (
                    "Dit is het beroemdste schilderij van Nederland. "
                    "Rembrandt schilderde het in opdracht van de schutterij. "
                    "Het toont een groep schutters die uitrukt onder aanvoering "
                    "van kapitein Frans Banninck Cocq en zijn luitenant. "
                ) * 3,
                "links": {"web": "https://www.rijksmuseum.nl/nl/collectie/SK-C-5"},
            },
            {
                "objectNumber": "SK-A-1",
                "title": "Portret van een man",
                "plaqueDescriptionDutch": "Een vroeg portret uit de Hollandse Gouden Eeuw. " * 5,
                "links": {},
            },
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "rijksmuseum_page_000000.json").write_text(
            _json.dumps(page_data), encoding="utf-8"
        )
        docs = list(parse_rijksmuseum_dir(p))

    assert len(docs) >= 2
    d = docs[0]
    assert d.meta.source == "rijksmuseum"
    assert d.meta.license == "CC0"
    assert d.meta.country == "NL"
    assert d.meta.domain == "cultural_heritage"
    assert "Nachtwacht" in d.text or "schutters" in d.text


def test_parse_rijksmuseum_dir_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_rijksmuseum_dir(Path(tmp))) == []
