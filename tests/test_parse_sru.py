"""Tests for SRU XML parser."""
import tempfile
from pathlib import Path

from pipeline.parse.sru import (
    _records_from_sru_xml,
    _extract_text,
    parse_ecli_xml_file,
    parse_rechtspraak_files,
    parse_wetten_files,
)

# Minimal SRU response wrapping a simple record
_SRU_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>1</numberOfRecords>
  <records>
    <record>
      <recordSchema>gzd</recordSchema>
      <recordData>{inner}</recordData>
    </record>
  </records>
</searchRetrieveResponse>"""


def _make_sru(inner: str) -> bytes:
    return _SRU_TEMPLATE.format(inner=inner).encode("utf-8")


def test_records_from_sru_xml():
    xml = _make_sru("<dc:title xmlns:dc='http://purl.org/dc/elements/1.1/'>Test wet</dc:title>")
    records = list(_records_from_sru_xml(xml))
    assert len(records) == 1


def test_records_empty_response():
    xml = b"""<?xml version="1.0"?>
    <searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
      <numberOfRecords>0</numberOfRecords>
      <records/>
    </searchRetrieveResponse>"""
    assert list(_records_from_sru_xml(xml)) == []


def test_records_malformed_xml():
    assert list(_records_from_sru_xml(b"not xml at all")) == []


def test_extract_text_skips_identifier():
    import xml.etree.ElementTree as ET
    xml = """<data>
      <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Wet op de omzetbelasting</dc:title>
      <dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">https://example.com/bwbr001</dc:identifier>
      <tekst>De belasting wordt geheven over leveringen van goederen.</tekst>
    </data>"""
    elem = ET.fromstring(xml)
    text = _extract_text(elem)
    assert "Wet op de omzetbelasting" in text
    assert "belasting" in text
    # identifier URL should be skipped
    assert "https://example.com" not in text


def test_parse_rechtspraak_files_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_rechtspraak_files(Path(tmp)))
    assert docs == []


def test_parse_rechtspraak_files_with_atom_feed():
    """Fallback parser: Atom feed pages produce brief summary documents."""
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Rechtspraak.nl</title>
  <entry>
    <id>ECLI:NL:HR:2020:1234</id>
    <title>HR uitspraak 2020:1234</title>
    <summary>Het beroep in cassatie is ingesteld door appellant. De Hoge Raad
    overweegt dat het middel faalt. Het gaat hier om een juridische procedure.</summary>
    <updated>2020-06-15T00:00:00Z</updated>
  </entry>
</feed>"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "feed_page_0001.xml").write_bytes(atom)
        docs = list(parse_rechtspraak_files(Path(tmp)))
    assert len(docs) == 1
    assert docs[0].meta.source == "rechtspraak"
    assert docs[0].meta.license == "PD"
    assert docs[0].meta.country == "NL"
    assert "cassatie" in docs[0].text


def test_parse_ecli_xml_file():
    """ECLI XML files in decisions/ are parsed to full-text documents."""
    ecli_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:identifier>ECLI:NL:HR:2020:1234</dc:identifier>
  <dc:date>2020-06-15</dc:date>
  <dc:publisher>Hoge Raad</dc:publisher>
  <inhoud>Het beroep in cassatie is ingesteld door appellant. De Hoge Raad
  overweegt dat het middel faalt. Het gaat hier om een juridische procedure
  die betrekking heeft op het bestuursrecht en de uitleg van wetgeving.</inhoud>
</root>"""
    with tempfile.TemporaryDirectory() as tmp:
        xml_file = Path(tmp) / "ECLI_NL_HR_2020_1234.xml"
        xml_file.write_bytes(ecli_xml)
        doc = parse_ecli_xml_file(xml_file)
    assert doc is not None
    assert doc.meta.source == "rechtspraak"
    assert doc.meta.license == "PD"
    assert doc.meta.country == "NL"
    assert "cassatie" in doc.text


def test_parse_rechtspraak_files_prefers_decisions_dir():
    """decisions/ directory is used over Atom feed fallback when present."""
    ecli_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:identifier>ECLI:NL:RBAMS:2021:9999</dc:identifier>
  <dc:date>2021-03-01</dc:date>
  <inhoud>De rechtbank Amsterdam overweegt als volgt inzake het verzoek van
  eiser tot vernietiging van het bestreden besluit. Het beroep is gegrond.</inhoud>
</root>"""
    with tempfile.TemporaryDirectory() as tmp:
        decisions = Path(tmp) / "decisions"
        decisions.mkdir()
        (decisions / "ECLI_NL_RBAMS_2021_9999.xml").write_bytes(ecli_xml)
        # Also add an Atom feed page — should be ignored
        (Path(tmp) / "feed_page_0000.xml").write_bytes(b"<feed/>")
        docs = list(parse_rechtspraak_files(Path(tmp)))
    assert len(docs) == 1
    assert "gegrond" in docs[0].text


def test_parse_wetten_files_with_batch():
    inner = """
    <root xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title>Wet op de vennootschapsbelasting</dc:title>
      <dc:identifier>BWBR0002672</dc:identifier>
      <dc:date>1969-07-14</dc:date>
      <artikel>De vennootschapsbelasting wordt geheven van lichamen over hun belastbaar bedrag.</artikel>
    </root>"""
    xml = _make_sru(inner)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "wetten_batch_00000001.xml").write_bytes(xml)
        docs = list(parse_wetten_files(Path(tmp)))
    assert len(docs) == 1
    assert docs[0].meta.source == "wetten"
    assert "vennootschapsbelasting" in docs[0].text
