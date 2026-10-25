"""
Tests for: Wikibooks, Wikinews, DutchDraCor, Tatoeba, EMEA parsers.
"""
import bz2
import tempfile
from pathlib import Path

from pipeline.parse.wiki_dump import parse_wikibooks_dir, parse_wikinews_dir
from pipeline.parse.plain_text import parse_dracor_dir, parse_tatoeba_dir, parse_emea_dir


# ---------------------------------------------------------------------------
# Shared MediaWiki dump helper (same as test_new_sources.py)
# ---------------------------------------------------------------------------

def _make_wiki_dump(title: str, text: str) -> bytes:
    xml = f"""<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <page>
    <title>{title}</title>
    <ns>0</ns>
    <id>99</id>
    <revision>
      <id>2001</id>
      <timestamp>2023-04-10T08:00:00Z</timestamp>
      <text>{text}</text>
    </revision>
  </page>
</mediawiki>""".encode("utf-8")
    return bz2.compress(xml)


_LONG_TEXT = (
    "Dit is een uitgebreide tekst over de Nederlandse taal en cultuur. "
    "Het Nederlands is een West-Germaanse taal die wordt gesproken door "
    "circa 24 miljoen mensen in Nederland, België en Suriname. "
    "De taal heeft een rijke literaire traditie met auteurs als Multatuli, "
    "Louis Couperus en Harry Mulisch. Het Nederlandse culturele erfgoed "
    "omvat ook de Gouden Eeuw met schilders als Rembrandt en Vermeer."
)


# ---------------------------------------------------------------------------
# Wikibooks
# ---------------------------------------------------------------------------

def test_parse_wikibooks_yields_document():
    dump = _make_wiki_dump("Wiskunde voor beginners", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nlwikibooks-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_wikibooks_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "wikibooks"
    assert d.meta.license == "CC-BY-SA"
    assert d.meta.domain == "educational"
    assert "nl.wikibooks.org" in d.meta.source_url


def test_parse_wikibooks_no_dump():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_wikibooks_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Wikinews
# ---------------------------------------------------------------------------

def test_parse_wikinews_yields_document():
    dump = _make_wiki_dump("Kabinet presenteert nieuwe plannen", _LONG_TEXT)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nlwikinews-latest-pages-articles.xml.bz2").write_bytes(dump)
        docs = list(parse_wikinews_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "wikinews"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "news"
    assert "nl.wikinews.org" in d.meta.source_url


def test_parse_wikinews_skips_redirects():
    dump = _make_wiki_dump("Redirect", "#redirect [[Hoofdartikel]]")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "dump.xml.bz2").write_bytes(dump)
        docs = list(parse_wikinews_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# DutchDraCor
# ---------------------------------------------------------------------------

_TEI_PLAY = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>De Spaanschen Brabander</title>
      </titleStmt>
    </fileDesc>
    <profileDesc>
      <creation>
        <date when="1617"/>
      </creation>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div type="act">
        <div type="scene">
          <sp who="Jerolimo">
            <speaker>Jerolimo</speaker>
            <l>Rampzalig Antwerpen, o stad vol ongeval!</l>
            <l>Hoe heeft het noodlot u geveld met zwaar verval!</l>
          </sp>
          <sp who="Robbeknol">
            <speaker>Robbeknol</speaker>
            <p>Och baas, hier is brood noch geld, en 'k heb zo'n honger.</p>
          </sp>
          <stage>Robbeknol kijkt naar de lege zak.</stage>
          <sp who="Jerolimo">
            <speaker>Jerolimo</speaker>
            <l>Gij vraagt naar wat ik zelf niet heb, arm schaap.</l>
          </sp>
        </div>
      </div>
    </body>
  </text>
</TEI>""".encode("utf-8")


def test_parse_dracor_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        tei = p / "tei"
        tei.mkdir()
        (tei / "bredero-spaanschen-brabander.xml").write_bytes(_TEI_PLAY)
        docs = list(parse_dracor_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "dracor"
    assert d.meta.license == "CC0"
    assert d.meta.domain == "literature"
    assert d.meta.publication_year == 1617
    assert "Jerolimo" in d.text or "Antwerpen" in d.text


def test_parse_dracor_no_tei_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_dracor_dir(Path(tmp)))
    assert docs == []


def test_parse_dracor_skips_short():
    tiny_tei = b"""<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader><fileDesc><titleStmt><title>X</title></titleStmt></fileDesc></teiHeader>
  <text><body><div><sp><p>Kort.</p></sp></div></body></text>
</TEI>"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "tei").mkdir()
        (p / "tei" / "tiny.xml").write_bytes(tiny_tei)
        docs = list(parse_dracor_dir(p))
    assert docs == []


# ---------------------------------------------------------------------------
# Tatoeba
# ---------------------------------------------------------------------------

def _make_tatoeba_tsv(sentences: list[str]) -> bytes:
    lines = [f"{i+1}\tnld\t{s}" for i, s in enumerate(sentences)]
    return "\n".join(lines).encode("utf-8")


def test_parse_tatoeba_yields_documents():
    sents = [f"Dit is een voorbeeldzin nummer {i} in het Nederlands." for i in range(150)]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nld_sentences.tsv").write_bytes(_make_tatoeba_tsv(sents))
        docs = list(parse_tatoeba_dir(p))
    assert len(docs) >= 1
    assert all(d.meta.source == "tatoeba" for d in docs)
    assert all(d.meta.license == "CC-BY" for d in docs)
    assert all(d.meta.domain == "general" for d in docs)


def test_parse_tatoeba_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_tatoeba_dir(Path(tmp)))
    assert docs == []


def test_parse_tatoeba_skips_empty_lines():
    content = b"1\tnld\tEen zin.\n\n2\tnld\t\n3\tnld\tNog een zin.\n"
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nld_sentences.tsv").write_bytes(content)
        # Only 2 real sentences, below batch size — produces 0 or 1 doc
        docs = list(parse_tatoeba_dir(p))
    # 2 sentences → under batch threshold of 100, yields 1 partial doc
    assert len(docs) <= 1


# ---------------------------------------------------------------------------
# EMEA
# ---------------------------------------------------------------------------

def _make_emea_txt(n_lines: int) -> bytes:
    lines = [
        f"Het geneesmiddel {i} bevat de werkzame stof diclofenac-natrium."
        for i in range(n_lines)
    ]
    return "\n".join(lines).encode("utf-8")


def test_parse_emea_yields_documents():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nl.txt").write_bytes(_make_emea_txt(600))
        docs = list(parse_emea_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "emea"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "medical"
    assert "diclofenac" in d.text


def test_parse_emea_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_emea_dir(Path(tmp)))
    assert docs == []


def test_parse_emea_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "nl.txt").write_bytes(b"Kort.\n")
        docs = list(parse_emea_dir(p))
    assert docs == []
