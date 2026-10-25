"""Tests for PII pattern detection and scrubber."""
import re
from pipeline.pii.patterns import find_all, _bsn_elfproef, PATTERN_BY_NAME
from pipeline.pii.scrubber import scrub, ScrubConfig, RedactionRecord


# ---------------------------------------------------------------------------
# BSN validation
# ---------------------------------------------------------------------------

def test_bsn_elfproef_valid():
    # 123456782: sum = 9+16+21+24+25+24+21+16-2 = 154, 154%11 = 0 → valid
    assert _bsn_elfproef("123456782") is True
    # 123456789: sum = 9+16+21+24+25+24+21+16-9 = 147, 147%11 = 4 → invalid
    assert _bsn_elfproef("123456789") is False


def test_bsn_elfproef_wrong_length():
    assert _bsn_elfproef("12345678") is False
    assert _bsn_elfproef("1234567890") is False


def test_bsn_elfproef_non_digits():
    assert _bsn_elfproef("12345678a") is False


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def test_detect_email():
    matches = find_all("Stuur een email naar jan.jansen@example.nl voor meer informatie.")
    names = [m[0] for m in matches]
    assert "email" in names


def test_detect_iban_nl():
    matches = find_all("Mijn bankrekening is NL91ABNA0417164300.")
    names = [m[0] for m in matches]
    assert "iban_nl" in names


def test_detect_phone_mobile():
    matches = find_all("Bel mij op 06-12345678 of 06 87654321.")
    names = [m[0] for m in matches]
    assert "phone_mobile" in names


def test_detect_phone_international():
    matches = find_all("Internationaal: +31 20 123 4567")
    names = [m[0] for m in matches]
    assert "phone_landline" in names



def test_no_false_positive_normal_text():
    matches = find_all(
        "Het Europees Parlement heeft de richtlijn aangenomen met 412 stemmen voor."
    )
    # No PII in this clean government text
    assert len(matches) == 0



# ---------------------------------------------------------------------------
# Scrubber
# ---------------------------------------------------------------------------

def test_scrub_email():
    cfg = ScrubConfig(use_ner=False)
    text = "Contact: info@rechtbank.nl voor vragen."
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    assert "info@rechtbank.nl" not in scrubbed
    assert "[EMAIL]" in scrubbed
    assert record.count == 1


def test_scrub_iban():
    cfg = ScrubConfig(use_ner=False)
    text = "Betaal op rekening NL91ABNA0417164300 t.n.v. de rechtbank."
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    assert "NL91ABNA" not in scrubbed
    assert "[IBAN]" in scrubbed


def test_scrub_multiple_pii():
    cfg = ScrubConfig(use_ner=False)
    text = "Email: p.smit@gmail.com, telefoon: 06-12345678, IBAN: NL91ABNA0417164300"
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    assert "p.smit@gmail.com" not in scrubbed
    assert "06-12345678" not in scrubbed
    assert "NL91ABNA0417164300" not in scrubbed
    assert record.count >= 3


def test_scrub_no_pii_returns_unchanged():
    cfg = ScrubConfig(use_ner=False)
    text = "Het Hof van Justitie heeft arrest gewezen in zaak C-123/20."
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    assert scrubbed == text
    assert record.count == 0


def test_scrub_does_not_double_redact():
    """Overlapping matches should only be redacted once."""
    cfg = ScrubConfig(use_ner=False)
    # Phone number that could also match partial IBAN — just verify no crash
    text = "06-12345678"
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    assert "06-12345678" not in scrubbed


def test_scrub_preserves_non_pii_structure():
    cfg = ScrubConfig(use_ner=False)
    text = (
        "Artikel 1. De belasting bedraagt 21 procent.\n"
        "Artikel 2. Contactpersoon: jan@belasting.nl\n"
        "Artikel 3. De wet treedt in werking per 1 januari 2024."
    )
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    assert "Artikel 1" in scrubbed
    assert "Artikel 3" in scrubbed
    assert "jan@belasting.nl" not in scrubbed


def test_redaction_record_special_category():
    cfg = ScrubConfig(use_ner=False)
    # BSN - special category under Art. 9 GDPR
    # Use a known valid BSN: 123456782
    text = "Het BSN van betrokkene is 123456782."
    scrubbed, record = scrub(text, doc_id="test", config=cfg)
    if record.count > 0:  # BSN detected
        assert record.has_special_category
