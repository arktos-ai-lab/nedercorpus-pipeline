"""
PII audit log writer.

Writes a JSONL audit trail of all documents processed by the scrubber,
suitable for:
- GDPR Article 30 (records of processing activities)
- EU AI Act Article 10 (data governance documentation)
- Internal compliance review

Format: one JSON object per document, written to audit/pii_redactions.jsonl

Integrity: each entry contains a SHA-256 hash of the previous entry's
serialised JSON (sorted keys), forming a tamper-evident chain.
Any external modification of an entry will break the chain from that
point forward.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import uuid
from pathlib import Path

from pipeline.pii.scrubber import RedactionRecord

logger = logging.getLogger(__name__)


class PIIAuditLog:
    """
    Context-manager that appends PII redaction records to a JSONL file.

    Every document processed (zero or more redactions) is logged so that
    GDPR Art. 30 records are complete. The run_id groups all entries from
    a single pipeline invocation.
    """

    def __init__(self, audit_dir: Path, ner_available: bool = False) -> None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        self._path = audit_dir / "pii_redactions.jsonl"
        self._fh = self._path.open("a", encoding="utf-8")
        self._run_id = uuid.uuid4().hex
        self._ner_available = ner_available
        self._prev_hash = "0" * 64      # genesis hash for integrity chain

        self._total_docs = 0
        self._docs_with_redactions = 0
        self._total_redactions = 0
        self._special_category_docs = 0

        # Write a run-start marker so multiple runs in the same file are clear
        self._write_entry({
            "entry_type": "run_start",
            "run_id": self._run_id,
            "ner_available": self._ner_available,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

    def _write_entry(self, entry: dict) -> None:
        """Serialise entry, chain-hash it, and append to the audit file."""
        entry["sha256_prev"] = self._prev_hash
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        self._prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
        self._fh.write(line + "\n")

    def record(self, rec: RedactionRecord, source: str = "") -> None:
        """
        Log a processed document.

        Called for EVERY document — not just those with redactions — so
        that the audit trail is complete and non-selective (GDPR Art. 5(2)).
        """
        entry = {
            "entry_type": "document",
            "run_id": self._run_id,
            "doc_id": rec.doc_id,
            "source": source,
            "redaction_count": rec.count,
            "has_special_category_data": rec.has_special_category,
            "redactions": rec.redactions,
            "ner_available": self._ner_available,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._write_entry(entry)

        self._total_docs += 1
        if rec.count > 0:
            self._docs_with_redactions += 1
        self._total_redactions += rec.count
        if rec.has_special_category:
            self._special_category_docs += 1

    def summary(self) -> dict:
        return {
            "run_id": self._run_id,
            "total_docs_processed": self._total_docs,
            "docs_with_redactions": self._docs_with_redactions,
            "total_redactions": self._total_redactions,
            "docs_with_special_category_data": self._special_category_docs,
            "ner_available": self._ner_available,
        }

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()
        summary = self.summary()
        # Write timestamped summary to preserve historical runs
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_summary_path = self._path.parent / f"pii_summary_{self._run_id[:8]}_{ts}.json"
        run_summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Also write/update the canonical latest summary for easy access
        latest_path = self._path.parent / "pii_summary.json"
        latest_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("PII audit log : %s", self._path)
        logger.info("PII summary   : %s", latest_path)

    def __enter__(self) -> "PIIAuditLog":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def load_summary(audit_dir: Path) -> dict:
    path = audit_dir / "pii_summary.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def verify_chain(audit_file: Path) -> tuple[bool, int, str]:
    """
    Verify the SHA-256 integrity chain of an audit JSONL file.

    Returns (ok, entries_checked, first_broken_entry_id).
    An unbroken chain means no entry was tampered with after writing.
    """
    prev_hash = "0" * 64
    entries = 0
    with audit_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return False, entries, f"unparseable line {entries + 1}"

            stored_prev = entry.get("sha256_prev", "")
            if stored_prev != prev_hash:
                doc_id = entry.get("doc_id", entry.get("entry_type", "?"))
                return False, entries, doc_id

            # Recompute hash: reconstruct entry as it was when written
            # (sha256_prev was set before serialisation, so the stored line
            # already contains it — hash the stored line directly)
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
            entries += 1

    return True, entries, ""
