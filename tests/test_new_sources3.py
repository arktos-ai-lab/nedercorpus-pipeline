"""
Tests for: OPUS extra, UD Dutch, OpenBoek, Dutch Renaissance Poetry parsers.
"""
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import (
    parse_opus_extra_dir,
    parse_ud_dutch_dir,
    parse_openboek_dir,
    parse_renaissance_poetry_dir,
)

_DUTCH_TEXT = (
    "De Nederlandse taal heeft een rijke geschiedenis die teruggaat tot "
    "de vroege middeleeuwen. Het is een West-Germaanse taal die nauw verwant "
    "is aan het Duits en het Engels. Tegenwoordig wordt het gesproken door "
    "circa 24 miljoen mensen in Nederland, België en Suriname."
)


# ---------------------------------------------------------------------------
# OPUS extra
# ---------------------------------------------------------------------------

def _make_opus_txt(n_lines: int) -> bytes:
    lines = [f"De Europese Unie heeft verordening {i} vastgesteld betreffende milieunormen." for i in range(n_lines)]
    return "\n".join(lines).encode("utf-8")


def test_parse_opus_extra_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        corpus_dir = p / "euconst"
        corpus_dir.mkdir()
        (corpus_dir / "nl.txt").write_bytes(_make_opus_txt(600))
        docs = list(parse_opus_extra_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "opus_extra"
    assert d.meta.license == "PD"
    assert d.meta.domain == "legal"
    assert d.meta.country == "EU"
    assert "Europese" in d.text


def test_parse_opus_extra_kde4_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        corpus_dir = p / "kde4"
        corpus_dir.mkdir()
        (corpus_dir / "nl.txt").write_bytes(_make_opus_txt(600))
        docs = list(parse_opus_extra_dir(p))
    assert len(docs) >= 1
    assert docs[0].meta.license == "CC-BY-SA"
    assert docs[0].meta.domain == "software"


def test_parse_opus_extra_multiple_corpora():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for corpus in ("euconst", "kde4", "php"):
            d = p / corpus
            d.mkdir()
            (d / "nl.txt").write_bytes(_make_opus_txt(600))
        docs = list(parse_opus_extra_dir(p))
    # Each corpus should produce at least 1 doc
    sources = {d.meta.source for d in docs}
    assert "opus_extra" in sources
    assert len(docs) >= 3


def test_parse_opus_extra_no_subdirs():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_opus_extra_dir(Path(tmp)))
    assert docs == []


def test_parse_opus_extra_missing_nl_txt():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "euconst").mkdir()
        # No nl.txt inside
        docs = list(parse_opus_extra_dir(p))
    assert docs == []


# ---------------------------------------------------------------------------
# UD Dutch
# ---------------------------------------------------------------------------

def _make_ud_sentences(n: int) -> bytes:
    lines = [f"De rechter heeft het vonnis uitgesproken in zaak nummer {i}." for i in range(n)]
    return "\n".join(lines).encode("utf-8")


def test_parse_ud_dutch_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        tb_dir = p / "alpino"
        tb_dir.mkdir()
        (tb_dir / "sentences.txt").write_bytes(_make_ud_sentences(300))
        docs = list(parse_ud_dutch_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "ud_dutch"
    assert d.meta.license == "CC-BY-SA"
    assert d.meta.domain == "general"
    assert "rechter" in d.text


def test_parse_ud_dutch_alpino_url():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        tb_dir = p / "alpino"
        tb_dir.mkdir()
        (tb_dir / "sentences.txt").write_bytes(_make_ud_sentences(300))
        docs = list(parse_ud_dutch_dir(p))
    assert "Alpino" in docs[0].meta.source_url


def test_parse_ud_dutch_lassy_small():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        tb_dir = p / "lassy_small"
        tb_dir.mkdir()
        (tb_dir / "sentences.txt").write_bytes(_make_ud_sentences(300))
        docs = list(parse_ud_dutch_dir(p))
    assert len(docs) >= 1
    assert "LassySmall" in docs[0].meta.source_url


def test_parse_ud_dutch_no_sentences_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "alpino").mkdir()
        docs = list(parse_ud_dutch_dir(p))
    assert docs == []


# ---------------------------------------------------------------------------
# OpenBoek
# ---------------------------------------------------------------------------

def _make_openboek_txt() -> bytes:
    return (_DUTCH_TEXT * 10).encode("utf-8")


def test_parse_openboek_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        (texts_dir / "hermans-nooit-meer-slapen.txt").write_bytes(_make_openboek_txt())
        docs = list(parse_openboek_dir(p))
    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "openboek"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "literature"
    assert "openboek" in d.meta.source_url


def test_parse_openboek_multiple_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        for i in range(3):
            (texts_dir / f"boek_{i}.txt").write_bytes(_make_openboek_txt())
        docs = list(parse_openboek_dir(p))
    assert len(docs) == 3


def test_parse_openboek_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        (texts_dir / "tiny.txt").write_bytes(b"Kort.")
        docs = list(parse_openboek_dir(p))
    assert docs == []


def test_parse_openboek_no_texts_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_openboek_dir(Path(tmp)))
    assert docs == []


# ---------------------------------------------------------------------------
# Dutch Renaissance Poetry
# ---------------------------------------------------------------------------

_POETRY_TEXT = (
    "O Nederland, vol glorij en eer,\n"
    "Gij zijt van ouds een land van dicht en lied,\n"
    "Waar elke dichter in zijn kunst zijn heer\n"
    "Met trotse woorden huldigt en beziet.\n"
    "De muzen dalen neer op Hollands grond,\n"
    "En spreken door de monden van geleerd.\n"
) * 5  # repeat to exceed min length


def test_parse_renaissance_poetry_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        (texts_dir / "hooft_sonnetten.txt").write_bytes(_POETRY_TEXT.encode("utf-8"))
        docs = list(parse_renaissance_poetry_dir(p))
    assert len(docs) >= 1
    d = docs[0]
    assert d.meta.source == "renaissance_poetry"
    assert d.meta.license == "CC0"
    assert d.meta.domain == "literature"
    assert "mirsdes" in d.meta.source_url


def test_parse_renaissance_poetry_multiple_formats():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        (texts_dir / "poem.txt").write_bytes(_POETRY_TEXT.encode("utf-8"))
        # CSV file (e.g. the repo uses this format for some files)
        csv_content = "id,text\n1,\"Een versregel uit de renaissance.\"\n"
        (texts_dir / "data.csv").write_bytes((csv_content * 20).encode("utf-8"))
        docs = list(parse_renaissance_poetry_dir(p))
    assert len(docs) >= 1


def test_parse_renaissance_poetry_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        texts_dir = p / "texts"
        texts_dir.mkdir()
        (texts_dir / "fragment.txt").write_bytes(b"Kort vers.")
        docs = list(parse_renaissance_poetry_dir(p))
    assert docs == []


def test_parse_renaissance_poetry_no_texts_dir():
    with tempfile.TemporaryDirectory() as tmp:
        docs = list(parse_renaissance_poetry_dir(Path(tmp)))
    assert docs == []
