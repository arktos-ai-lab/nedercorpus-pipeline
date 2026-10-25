"""
Tests for new source parsers: Staatscourant (sru.py) and Wikisource (wiki_dump.py).
"""
import bz2
import tempfile
from pathlib import Path

from pipeline.parse.sru import parse_staatscourant_files
from pipeline.parse.wiki_dump import parse_wikisource_dir


# ---------------------------------------------------------------------------
# Staatscourant
# ---------------------------------------------------------------------------

def _make_stcrt_batch(title: str, body: str, identifier: str, date: str) -> bytes:
    """Build a minimal SRU GZD response XML containing one Staatscourant record."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
  <srw:numberOfRecords>1</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <gzd:gzd xmlns:gzd="http://standaarden.overheid.nl/sru"
                  xmlns:dc="http://purl.org/dc/elements/1.1/"
                  xmlns:dct="http://purl.org/dc/terms/">
          <dc:title>{title}</dc:title>
          <dc:identifier>{identifier}</dc:identifier>
          <dc:date>{date}</dc:date>
          <dct:description>{body}</dct:description>
        </gzd:gzd>
      </srw:recordData>
    </srw:record>
  </srw:records>
</searchRetrieveResponse>""".encode("utf-8")


def test_parse_staatscourant_files_yields_document():
    xml = _make_stcrt_batch(
        title="Besluit van de Minister van Economische Zaken",
        body=(
            "De Minister van Economische Zaken en Klimaat maakt bekend dat de "
            "subsidieregeling voor duurzame energie wordt verlengd tot 31 december 2025. "
            "Bedrijven die investeren in zonne-energie komen in aanmerking voor de regeling."
        ),
        identifier="https://zoek.officielebekendmakingen.nl/stcrt-2024-12345.html",
        date="2024-03-01",
    )
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "stcrt_batch_00000001.xml").write_bytes(xml)
        docs = list(parse_staatscourant_files(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "staatscourant"
    assert d.meta.license == "PD"
    assert d.meta.country == "NL"
    assert d.meta.domain == "government"
    assert d.meta.publication_year == 2024
    assert "subsidieregeling" in d.text or "Economische Zaken" in d.text


def test_parse_staatscourant_files_skips_short():
    xml = _make_stcrt_batch(
        title="x", body="Te kort.", identifier="https://example.com", date="2024-01-01"
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "stcrt_batch_00000001.xml").write_bytes(xml)
        docs = list(parse_staatscourant_files(Path(tmp)))
    assert docs == []


def test_parse_staatscourant_files_ignores_non_stcrt():
    """Non-batch files are ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "unrelated.xml").write_bytes(b"<x/>")
        docs = list(parse_staatscourant_files(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Wikisource
# ---------------------------------------------------------------------------

def _make_wikisource_dump(title: str, text: str) -> bytes:
    """Build a minimal bz2-compressed MediaWiki XML dump with one article."""
    xml = f"""<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <page>
    <title>{title}</title>
    <ns>0</ns>
    <id>42</id>
    <revision>
      <id>1001</id>
      <timestamp>2022-06-15T10:00:00Z</timestamp>
      <text>{text}</text>
    </revision>
  </page>
</mediawiki>""".encode("utf-8")
    return bz2.compress(xml)


def test_parse_wikisource_dir_yields_document():
    title = "Max Havelaar"
    body = (
        "Max Havelaar is een Nederlandse roman van Multatuli uit 1860. "
        "Het boek beschrijft de misstanden in de Nederlandse koloniale administratie "
        "van Nederlands-Indië. De hoofdpersoon Max Havelaar strijdt voor gerechtigheid "
        "voor de inheemse bevolking van Java. Dit werk wordt beschouwd als een van de "
        "meest invloedrijke Nederlandse romans ooit geschreven."
    )
    dump_bytes = _make_wikisource_dump(title, body)

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nlwikisource-latest-pages-articles.xml.bz2").write_bytes(dump_bytes)
        docs = list(parse_wikisource_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "wikisource"
    assert d.meta.license == "CC-BY-SA"
    assert d.meta.domain == "literature"
    assert "nl.wikisource.org" in d.meta.source_url
    assert "Multatuli" in d.text


def test_parse_wikisource_dir_skips_redirects():
    dump_bytes = _make_wikisource_dump(
        "Doorverwijzing", "#doorverwijzing [[Max Havelaar]]"
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "dump.xml.bz2").write_bytes(dump_bytes)
        docs = list(parse_wikisource_dir(Path(tmp)))
    assert docs == []


def test_parse_wikisource_dir_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_wikisource_dir(Path(tmp)))
    assert docs == []
