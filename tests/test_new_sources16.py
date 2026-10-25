"""
Tests for: dekamer parser.
"""
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import parse_dekamer_dir

_HTML_NL = """<html><body>
<p>De voorzitter: Ik open de vergadering. Het woord is aan de heer Jansen.</p>
<p>De heer Jansen: Mevrouw de voorzitter, ik wil vandaag spreken over het beleid inzake volkshuisvesting. Dit is een belangrijk onderwerp voor de Nederlandse gemeenschap in ons land.</p>
<p>De heer Jansen: De situatie is zorgwekkend en vraagt om dringende maatregelen van de bevoegde ministers. Het is noodzakelijk dat de Kamer zich hierover uitspreekt.</p>
<p>Le président: La parole est à M. Dubois. (French section — should be filtered out)</p>
<p>M. Dubois: Je voudrais parler de la politique du logement. C'est un sujet très important pour notre communauté.</p>
<p>De voorzitter: Dank u, mijnheer Jansen. Zijn er andere sprekers over dit agendapunt?</p>
</body></html>"""

_HTML_FRENCH_ONLY = """<html><body>
<p>Le président: La séance est ouverte. La parole est à M. Dubois.</p>
<p>M. Dubois: Je voudrais parler de la politique du logement. C'est un sujet très important pour notre communauté française.</p>
<p>M. Martin: Je suis tout à fait d'accord avec mon collègue. Cette question mérite une attention particulière de notre assemblée.</p>
<p>Le président: Merci. Y a-t-il d'autres intervenants sur ce point de l'ordre du jour?</p>
</body></html>"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_parse_dekamer_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "dekamer_leg55_ip001.html").write_text(_HTML_NL, encoding="utf-8")
        docs = list(parse_dekamer_dir(p))

    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "dekamer"
    assert d.meta.license == "PD"
    assert d.meta.country == "BE"
    assert d.meta.domain == "parliamentary"
    assert d.meta.language_variant == "NL-BE"


def test_parse_dekamer_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_dekamer_dir(Path(tmp))) == []


def test_parse_dekamer_extracts_legislature():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "dekamer_leg55_ip001.html").write_text(_HTML_NL, encoding="utf-8")
        docs = list(parse_dekamer_dir(p))

    assert len(docs) >= 1
    # Legislature 55 covers 2019-2024; pub_year should be 2019 (start year)
    assert docs[0].meta.publication_year is not None
    assert 2019 <= docs[0].meta.publication_year <= 2030


def test_parse_dekamer_filters_french():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "dekamer_leg55_ip001.html").write_text(_HTML_FRENCH_ONLY, encoding="utf-8")
        docs = list(parse_dekamer_dir(p))

    # Mostly French content: either 0 docs, or text is very short (skipped)
    # With strict Dutch filtering the French-only session should produce nothing
    assert len(docs) == 0


def test_parse_dekamer_multiple_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(1, 4):
            (p / f"dekamer_leg55_ip{i:03d}.html").write_text(_HTML_NL, encoding="utf-8")
        docs = list(parse_dekamer_dir(p))

    assert len(docs) == 3
