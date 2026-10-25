"""
Tests for: uvn_podcast, uvv_podcast, debalie_commons parsers.
"""
import json
import tempfile
from pathlib import Path

from pipeline.parse.plain_text import (
    parse_uvn_podcast_dir,
    parse_uvv_podcast_dir,
    parse_debalie_commons_dir,
)


# ---------------------------------------------------------------------------
# Shared fixture text (long enough to pass 200-char filter)
# ---------------------------------------------------------------------------

_LECTURE_TEXT = (
    "In deze aflevering bespreken we de grondbeginselen van de sterrenkunde. "
    "We kijken naar de vorming van sterren, planeten en sterrenstelsels. "
    "Astronomen gebruiken telescopen en spectrografen om het heelal te bestuderen. "
    "De Big Bang theorie verklaart het ontstaan van het universum 13,8 miljard jaar geleden. "
) * 3

_CULTURE_TEXT = (
    "De Balie is een podium voor politiek, cultuur en wetenschap in Amsterdam. "
    "In dit debat bespreken we de toekomst van de democratie in Europa. "
    "Sprekers van verschillende politieke partijen wisselen van gedachten. "
    "Het publiek stelt vragen over actuele maatschappelijke onderwerpen. "
) * 3


# ---------------------------------------------------------------------------
# uvn_podcast
# ---------------------------------------------------------------------------

def test_parse_uvn_podcast_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        ep_id = "sterrenkunde_in_de_21e_eeuw"
        (p / f"{ep_id}.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        (p / f"{ep_id}.json").write_text(json.dumps({
            "source": "uvn_podcast",
            "ep_id": ep_id,
            "title": "Sterrenkunde in de 21e eeuw",
            "source_url": "https://www.universiteitvannederland.nl/college/sterrenkunde",
            "license": "CC-BY",
            "publication_year": 2022,
            "language_variant": "NL",
            "country": "NL",
            "domain": "speech",
        }), encoding="utf-8")
        docs = list(parse_uvn_podcast_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "uvn_podcast"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "speech"
    assert d.meta.country == "NL"
    assert d.meta.language_variant == "NL"
    assert d.meta.publication_year == 2022
    assert "sterrenkunde" in d.text.lower() or "Big Bang" in d.text


def test_parse_uvn_podcast_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_uvn_podcast_dir(Path(tmp))) == []


def test_parse_uvn_podcast_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "short_ep.txt").write_text("Te kort.", encoding="utf-8")
        (p / "long_ep.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        docs = list(parse_uvn_podcast_dir(p))

    assert len(docs) == 1


def test_parse_uvn_podcast_skips_underscore_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "_meta.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        (p / "valid_ep.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        docs = list(parse_uvn_podcast_dir(p))

    assert len(docs) == 1


def test_parse_uvn_podcast_no_sidecar():
    """Parser must work without a .json sidecar (defaults to CC-BY, country NL)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "some_episode.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        docs = list(parse_uvn_podcast_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "uvn_podcast"
    assert d.meta.license == "CC-BY"
    assert d.meta.country == "NL"


# ---------------------------------------------------------------------------
# uvv_podcast
# ---------------------------------------------------------------------------

def test_parse_uvv_podcast_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        ep_id = "klimaat_en_samenleving"
        (p / f"{ep_id}.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        (p / f"{ep_id}.json").write_text(json.dumps({
            "source": "uvv_podcast",
            "ep_id": ep_id,
            "title": "Klimaat en samenleving",
            "source_url": "https://www.universiteitvanvlaanderen.be/podcast/klimaat",
            "license": "CC-BY",
            "publication_year": 2021,
            "language_variant": "NL-BE",
            "country": "BE",
            "domain": "speech",
        }), encoding="utf-8")
        docs = list(parse_uvv_podcast_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "uvv_podcast"
    assert d.meta.license == "CC-BY"
    assert d.meta.domain == "speech"
    assert d.meta.country == "BE"
    assert d.meta.language_variant == "NL-BE"
    assert d.meta.publication_year == 2021


def test_parse_uvv_podcast_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_uvv_podcast_dir(Path(tmp))) == []


def test_parse_uvv_podcast_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "short_ep.txt").write_text("Kort.", encoding="utf-8")
        (p / "long_ep.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        docs = list(parse_uvv_podcast_dir(p))

    assert len(docs) == 1


def test_parse_uvv_podcast_no_sidecar():
    """Parser defaults to CC-BY, country BE, language_variant NL-BE."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "een_aflevering.txt").write_text(_LECTURE_TEXT, encoding="utf-8")
        docs = list(parse_uvv_podcast_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "uvv_podcast"
    assert d.meta.country == "BE"
    assert d.meta.language_variant == "NL-BE"


# ---------------------------------------------------------------------------
# debalie_commons
# ---------------------------------------------------------------------------

def test_parse_debalie_commons_yields_document():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        slug = "de_balie_democratie_debat"
        (p / f"{slug}.txt").write_text(_CULTURE_TEXT, encoding="utf-8")
        (p / f"{slug}.json").write_text(json.dumps({
            "source": "debalie_commons",
            "file_title": "File:De Balie - Democratie debat.webm",
            "slug": slug,
            "source_url": "https://commons.wikimedia.org/wiki/File:De_Balie_-_Democratie_debat.webm",
            "license": "CC-BY-3.0",
            "publication_year": 2019,
            "language_variant": "NL",
            "country": "NL",
            "domain": "culture",
        }), encoding="utf-8")
        docs = list(parse_debalie_commons_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "debalie_commons"
    assert d.meta.license == "CC-BY-3.0"
    assert d.meta.domain == "culture"
    assert d.meta.country == "NL"
    assert d.meta.language_variant == "NL"
    assert d.meta.publication_year == 2019
    assert "De Balie" in d.text or "debat" in d.text.lower()


def test_parse_debalie_commons_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(parse_debalie_commons_dir(Path(tmp))) == []


def test_parse_debalie_commons_skips_short():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "short_file.txt").write_text("Kort fragment.", encoding="utf-8")
        (p / "long_file.txt").write_text(_CULTURE_TEXT, encoding="utf-8")
        docs = list(parse_debalie_commons_dir(p))

    assert len(docs) == 1


def test_parse_debalie_commons_skips_underscore_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "_index.txt").write_text(_CULTURE_TEXT, encoding="utf-8")
        (p / "valid_file.txt").write_text(_CULTURE_TEXT, encoding="utf-8")
        docs = list(parse_debalie_commons_dir(p))

    assert len(docs) == 1


def test_parse_debalie_commons_no_sidecar():
    """Parser defaults to CC-BY-3.0 when no sidecar present."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "debat_2020.txt").write_text(_CULTURE_TEXT, encoding="utf-8")
        docs = list(parse_debalie_commons_dir(p))

    assert len(docs) == 1
    d = docs[0]
    assert d.meta.source == "debalie_commons"
    assert d.meta.license == "CC-BY-3.0"
    assert d.meta.country == "NL"
    assert d.meta.domain == "culture"


def test_parse_debalie_commons_multiple_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for i in range(3):
            slug = f"debat_{i:02d}"
            (p / f"{slug}.txt").write_text(_CULTURE_TEXT, encoding="utf-8")
            (p / f"{slug}.json").write_text(json.dumps({
                "source": "debalie_commons",
                "slug": slug,
                "license": "CC-BY-3.0",
                "publication_year": 2018 + i,
                "country": "NL",
                "domain": "culture",
                "language_variant": "NL",
            }), encoding="utf-8")
        docs = list(parse_debalie_commons_dir(p))

    assert len(docs) == 3
    years = {d.meta.publication_year for d in docs}
    assert years == {2018, 2019, 2020}
