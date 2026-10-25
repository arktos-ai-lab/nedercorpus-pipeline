"""
GDPR Article 17 — Right to Erasure ("Right to be Forgotten").

Provides a mechanism to remove a specific document from the final corpus
by document ID.  The caller supplies the corpus directory and the doc_id
to erase; this module locates and removes the document from all shard
files (Parquet or JSONL).

Usage:
    from pipeline.pii.erasure import erase_document
    erased = erase_document("rechtspraak:ECLI:NL:RB:2019:1234", corpus_dir)

The erasure is logged to <corpus_dir>/erasure_log.jsonl.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _erase_from_jsonl(doc_id: str, jsonl_path: Path) -> int:
    """Remove all lines with the given doc_id from a JSONL file. Returns count removed."""
    lines_in: list[str] = []
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            lines_in = f.readlines()
    except OSError as exc:
        logger.error("Cannot read %s: %s", jsonl_path, exc)
        return 0

    lines_out = []
    removed = 0
    for line in lines_in:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            lines_out.append(line)
            continue
        meta = obj.get("meta", {})
        if meta.get("id") == doc_id or obj.get("id") == doc_id:
            removed += 1
        else:
            lines_out.append(line)

    if removed:
        with jsonl_path.open("w", encoding="utf-8") as f:
            for line in lines_out:
                f.write(line + "\n")
        logger.info("Erased %d record(s) from %s", removed, jsonl_path.name)

    return removed


def _erase_from_parquet(doc_id: str, parquet_path: Path) -> int:
    """Remove the row with the given doc_id from a Parquet shard. Returns count removed."""
    try:
        import pyarrow.parquet as pq
        import pyarrow as pa
    except ImportError:
        logger.error(
            "pyarrow is required for Parquet erasure. "
            "Install: pip install pyarrow"
        )
        return 0

    table = pq.read_table(str(parquet_path))
    ids = table.column("id").to_pylist()
    mask = [i != doc_id for i in ids]
    removed = sum(1 for m in mask if not m)

    if removed:
        filtered = table.filter(pa.array(mask))
        pq.write_table(filtered, str(parquet_path), compression="zstd")
        logger.info("Erased %d record(s) from %s", removed, parquet_path.name)

    return removed


def erase_document(doc_id: str, corpus_dir: Path) -> bool:
    """
    Remove a document from the corpus by ID (GDPR Art. 17).

    Walks all JSONL and Parquet shard files in corpus_dir, removes any
    rows matching doc_id, and writes an entry to erasure_log.jsonl.

    Returns True if the document was found and removed from at least one file.
    """
    corpus_dir = Path(corpus_dir)
    total_removed = 0

    for jsonl_file in sorted(corpus_dir.rglob("*.jsonl")):
        if jsonl_file.name == "erasure_log.jsonl":
            continue
        total_removed += _erase_from_jsonl(doc_id, jsonl_file)

    for parquet_file in sorted(corpus_dir.rglob("*.parquet")):
        total_removed += _erase_from_parquet(doc_id, parquet_file)

    # Log the erasure request regardless of outcome (GDPR Art. 30 accountability)
    log_path = corpus_dir / "erasure_log.jsonl"
    entry = {
        "doc_id": doc_id,
        "corpus_dir": str(corpus_dir),
        "records_removed": total_removed,
        "success": total_removed > 0,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if total_removed:
        logger.info(
            "Art. 17 erasure: removed doc_id=%s from %d location(s).",
            doc_id, total_removed,
        )
    else:
        logger.warning(
            "Art. 17 erasure requested for doc_id=%s but document not found in %s.",
            doc_id, corpus_dir,
        )

    return total_removed > 0
