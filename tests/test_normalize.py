"""Tests for historical normalization."""
from pipeline.normalize.rules import apply_rules


def test_long_s():
    assert apply_rules("beſchrijving") == "beschrijving"


def test_long_s_multiple():
    # ſ→s, then ae→aa fires on "staet"→"staat"; "sijn" has no s→z rule (that lives in the lexicon)
    assert apply_rules("ſtaet ſijn") == "staat sijn"


def test_no_change_modern():
    text = "Dit is moderne Nederlandse tekst."
    assert apply_rules(text) == text
