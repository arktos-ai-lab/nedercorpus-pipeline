"""Extended tests for new PII patterns and compliance features."""
import tempfile
from pathlib import Path

from pipeline.pii.patterns import find_all, _luhn_check, PATTERN_BY_NAME
from pipeline.pii.scrubber import scrub, ScrubConfig, RedactionRecord
from pipeline.pii.audit import PIIAuditLog, verify_chain
from pipeline.pii.erasure import erase_document
from pipeline.document import Document, write_jsonl, make_id
from pipeline.schema import DocumentMeta


# ---------------------------------------------------------------------------
# Luhn algorithm
# ---------------------------------------------------------------------------

def test_luhn_valid_visa():
    # 4532015112830366 is a Luhn-valid test Visa number
    assert _luhn_check("4532015112830366") is True


def test_luhn_invalid():
    assert _luhn_check("4532015112830367") is False


def test_luhn_too_short():
    assert _luhn_check("12345678901") is False  # 11 digits, below minimum


def test_luhn_non_digits():
    assert _luhn_check("453201511283036X") is False


# ---------------------------------------------------------------------------
# New regex patterns
# ---------------------------------------------------------------------------


def test_detect_passport_contextual():
    matches = find_all("Paspoort nummer: AA123456B afgegeven op 1 januari 2020.")
    names = [m[0] for m in matches]
    assert "passport_id" in names


def test_no_passport_without_keyword():
    # Alphanumeric string without keyword should not trigger passport_id
    matches = find_all("Zaak AA123456B is afgedaan.")
    names = [m[0] for m in matches]
    assert "passport_id" not in names




def test_suriname_id_contextual():
    matches = find_all("Identiteitsnummer: 12345678 van betrokkene.")
    names = [m[0] for m in matches]
    assert "suriname_id" in names


def test_no_suriname_id_bare_number():
    # 8-digit number without keyword — no longer triggers suriname_id
    matches = find_all("Artikel 20190012 van de Grondwet van Suriname.")
    names = [m[0] for m in matches]
    assert "suriname_id" not in names


def test_credit_card_luhn_validated():
    # 4111111111111111 is Luhn-valid (standard Visa test number)
    matches = find_all("Betaald met creditcard 4111111111111111.")
    names = [m[0] for m in matches]
    assert "credit_card" in names


def test_credit_card_invalid_luhn_not_detected():
    # 4111111111111112 fails Luhn — should NOT be flagged as credit card
    matches = find_all("Getal 4111111111111112 is niet relevant.")
    names = [m[0] for m in matches]
    assert "credit_card" not in names


def test_phone_parenthesised_area_code():
    matches = find_all("Bel (020) 123 4567 voor informatie.")
    names = [m[0] for m in matches]
    assert "phone_landline" in names


# ---------------------------------------------------------------------------
# Scrubber: source-aware NER warning (only logs, no crash)
# ---------------------------------------------------------------------------

def test_scrub_sr_source_no_crash():
    """SR source without NER should warn but not crash."""
    cfg = ScrubConfig(use_ner=False, source_country="SR")
    text = "Staatsbesluit nr. 2023-001 van de Republiek Suriname."
    scrubbed, record = scrub(text, doc_id="sr_test", config=cfg)
    assert isinstance(scrubbed, str)



# ---------------------------------------------------------------------------
# Audit log: run_id, all-doc logging, integrity chain
# ---------------------------------------------------------------------------

def _make_redaction_record(doc_id: str, count: int = 0) -> RedactionRecord:
    rec = RedactionRecord(doc_id=doc_id)
    for i in range(count):
        rec.add("regex", "email", "[EMAIL]", 20)
    return rec


def test_audit_run_id_assigned():
    with tempfile.TemporaryDirectory() as tmp:
        audit_dir = Path(tmp) / "audit"
        with PIIAuditLog(audit_dir) as log:
            assert len(log._run_id) == 32  # uuid4().hex = 32 hex chars


def test_audit_logs_all_documents():
    """Every document is recorded, even those with zero redactions."""
    with tempfile.TemporaryDirectory() as tmp:
        audit_dir = Path(tmp) / "audit"
        with PIIAuditLog(audit_dir) as log:
            log.record(_make_redaction_record("doc1", count=0), "test")
            log.record(_make_redaction_record("doc2", count=2), "test")
            log.record(_make_redaction_record("doc3", count=0), "test")
            assert log._total_docs == 3
            assert log._docs_with_redactions == 1


def test_audit_summary_keys():
    with tempfile.TemporaryDirectory() as tmp:
        audit_dir = Path(tmp) / "audit"
        with PIIAuditLog(audit_dir) as log:
            log.record(_make_redaction_record("doc1", count=1), "test")
        summary_path = audit_dir / "pii_summary.json"
        assert summary_path.exists()
        import json
        summary = json.loads(summary_path.read_text())
        assert "run_id" in summary
        assert "total_docs_processed" in summary
        assert "docs_with_redactions" in summary
        assert "ner_available" in summary


def test_audit_integrity_chain_valid():
    with tempfile.TemporaryDirectory() as tmp:
        audit_dir = Path(tmp) / "audit"
        with PIIAuditLog(audit_dir) as log:
            log.record(_make_redaction_record("doc1", count=0), "test")
            log.record(_make_redaction_record("doc2", count=1), "test")
        audit_file = audit_dir / "pii_redactions.jsonl"
        ok, entries, first_broken = verify_chain(audit_file)
        assert ok, f"Chain broken at: {first_broken}"
        assert entries >= 3  # run_start + 2 document entries


def test_audit_integrity_chain_detects_tampering():
    import json
    with tempfile.TemporaryDirectory() as tmp:
        audit_dir = Path(tmp) / "audit"
        with PIIAuditLog(audit_dir) as log:
            log.record(_make_redaction_record("doc1"), "test")
            log.record(_make_redaction_record("doc2"), "test")
        audit_file = audit_dir / "pii_redactions.jsonl"
        # Tamper with the file: change doc_id in the second entry
        lines = audit_file.read_text(encoding="utf-8").splitlines()
        tampered = []
        for i, line in enumerate(lines):
            if i == 1:  # tamper the second entry
                obj = json.loads(line)
                obj["doc_id"] = "TAMPERED"
                line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
            tampered.append(line)
        audit_file.write_text("\n".join(tampered) + "\n", encoding="utf-8")
        ok, _, _ = verify_chain(audit_file)
        assert not ok, "Tampered chain should not verify"


# ---------------------------------------------------------------------------
# Erasure mechanism (Art. 17)
# ---------------------------------------------------------------------------

def _make_doc(doc_id_suffix: str) -> Document:
    meta = DocumentMeta(
        id=make_id("test", doc_id_suffix),
        source="test",
        source_url="",
        license="PD",
        publication_year=2023,
        country="NL",
        domain="legal",
        language_variant="NL",
    )
    return Document(text=f"Tekst van document {doc_id_suffix}.", meta=meta)


def test_erasure_removes_from_jsonl():
    docs = [_make_doc("a"), _make_doc("b"), _make_doc("c")]
    target_id = docs[1].meta.id
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp)
        write_jsonl(iter(docs), corpus_dir / "shard.jsonl")
        erased = erase_document(target_id, corpus_dir)
        assert erased is True
        # Remaining documents should not contain the erased ID
        from pipeline.document import read_jsonl
        remaining = list(read_jsonl(corpus_dir / "shard.jsonl"))
        assert all(d.meta.id != target_id for d in remaining)
        assert len(remaining) == 2


def test_erasure_not_found_returns_false():
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp)
        result = erase_document("nonexistent:doc", corpus_dir)
        assert result is False
        # Erasure log is written even for failed requests
        assert (corpus_dir / "erasure_log.jsonl").exists()


def test_erasure_log_written():
    docs = [_make_doc("x")]
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp)
        write_jsonl(iter(docs), corpus_dir / "shard.jsonl")
        erase_document(docs[0].meta.id, corpus_dir)
        import json
        log = corpus_dir / "erasure_log.jsonl"
        entry = json.loads(log.read_text().strip())
        assert entry["doc_id"] == docs[0].meta.id
        assert entry["success"] is True
        assert entry["records_removed"] == 1
