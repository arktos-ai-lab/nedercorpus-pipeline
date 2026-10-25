"""Tests for Wikipedia dump parser."""
import bz2
import tempfile
from pathlib import Path

from pipeline.parse.wiki_dump import _strip_wikitext, _is_redirect, parse_dump

MW_NS = "http://www.mediawiki.org/xml/export-0.11/"

def _make_dump(articles: list[dict], compress: bool = False) -> bytes:
    """Build a minimal MediaWiki XML dump."""
    pages = ""
    for i, art in enumerate(articles):
        ns   = art.get("ns", "0")
        title = art.get("title", f"Artikel {i}")
        pid  = art.get("id", str(i + 1))
        text = art.get("text", "")
        ts   = art.get("timestamp", "2023-01-01T00:00:00Z")
        pages += f"""
  <page>
    <title>{title}</title>
    <ns>{ns}</ns>
    <id>{pid}</id>
    <revision>
      <id>{i + 100}</id>
      <timestamp>{ts}</timestamp>
      <text xml:space="preserve">{text}</text>
    </revision>
  </page>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<mediawiki xmlns="{MW_NS}">
  <siteinfo><sitename>Wikipedia</sitename></siteinfo>
{pages}
</mediawiki>""".encode("utf-8")

    return bz2.compress(xml) if compress else xml


# ---------------------------------------------------------------------------
# Unit tests: wikitext stripping
# ---------------------------------------------------------------------------

def test_strip_bold_italic():
    assert "'''" not in _strip_wikitext("'''vetgedrukt''' en ''cursief''")
    assert "vetgedrukt" in _strip_wikitext("'''vetgedrukt''' en ''cursief''")

def test_strip_internal_links():
    result = _strip_wikitext("[[Amsterdam|de hoofdstad]]")
    assert "de hoofdstad" in result
    assert "[[" not in result

def test_strip_file_link():
    result = _strip_wikitext("[[File:foto.jpg|thumb|Een foto]] Tekst.")
    assert "File:" not in result
    assert "Tekst." in result

def test_strip_templates():
    result = _strip_wikitext("Zie {{bronvermelding|auteur=J. Jansen}} voor details.")
    assert "{{" not in result
    assert "voor details" in result

def test_strip_ref_tags():
    result = _strip_wikitext("Dit is bewezen.<ref>Bron: Jansen 2020.</ref>")
    assert "<ref>" not in result
    assert "Dit is bewezen" in result

def test_strip_section_headings():
    result = _strip_wikitext("== Geschiedenis ==\nDe stad is gesticht in 1234.")
    assert "==" not in result
    assert "Geschiedenis" in result

def test_is_redirect():
    assert _is_redirect("#DOORVERWIJZING [[Amsterdam]]")
    assert _is_redirect("#redirect [[test]]")
    assert not _is_redirect("Dit is een normaal artikel over Amsterdam.")


# ---------------------------------------------------------------------------
# Integration tests: dump parsing
# ---------------------------------------------------------------------------

def test_parse_dump_basic():
    body = "Amsterdam is de hoofdstad van Nederland. " * 10
    dump = _make_dump([{"title": "Amsterdam", "text": body}])
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump.xml"
        dump_path.write_bytes(dump)
        docs = list(parse_dump(dump_path))
    assert len(docs) == 1
    assert docs[0].meta.source == "wikipedia"
    assert docs[0].meta.license == "CC-BY-SA"
    assert "Amsterdam" in docs[0].text


def test_parse_dump_skips_redirect():
    dump = _make_dump([
        {"title": "AMS", "text": "#DOORVERWIJZING [[Amsterdam]]"},
        {"title": "Amsterdam", "text": "Amsterdam is de hoofdstad van Nederland. " * 10},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump.xml"
        dump_path.write_bytes(dump)
        docs = list(parse_dump(dump_path))
    assert len(docs) == 1
    assert docs[0].meta.id.endswith("Amsterdam") or "Amsterdam" in docs[0].text


def test_parse_dump_skips_non_main_namespace():
    body = "Overlegpagina inhoud. " * 20
    dump = _make_dump([{"title": "Overleg:Amsterdam", "ns": "1", "text": body}])
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump.xml"
        dump_path.write_bytes(dump)
        docs = list(parse_dump(dump_path))
    assert docs == []


def test_parse_dump_bz2():
    body = "Rotterdam is een grote haven. " * 15
    dump_bz2 = _make_dump([{"title": "Rotterdam", "text": body}], compress=True)
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump.xml.bz2"
        dump_path.write_bytes(dump_bz2)
        docs = list(parse_dump(dump_path))
    assert len(docs) == 1


def test_parse_dump_min_chars_filter():
    # Short article should be filtered out
    dump = _make_dump([{"title": "Korte artikel", "text": "Kort."}])
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "dump.xml"
        dump_path.write_bytes(dump)
        docs = list(parse_dump(dump_path, min_chars=200))
    assert docs == []
