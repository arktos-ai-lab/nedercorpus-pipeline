"""
Download the Belgian Journal (Belgisch Staatsblad company bylaws) dataset.

Source: HuggingFace guust-franssens/belgian-journal
  - 949K company bylaw records from the Moniteur Belge / Belgisch Staatsblad
  - Multilingual: Dutch, French, German (no explicit language field)
  - License: CC0-1.0
  - Content: company formation, dissolution, address changes, etc.

Strategy: download 20 Parquet shards, filter Dutch-language records using
a keyword heuristic (fast, no langdetect dependency), save as JSONL.

Dutch heuristic: text contains common Dutch legal/corporate keywords
(vennootschap, naamloze, besloten, rechtspersoon, oprichting, enz.).
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import ssl
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "belgian_journal"
LICENSE = "CC0"
COUNTRY = "BE"
DOMAIN  = "legal"

HF_BASE = "https://huggingface.co/datasets/guust-franssens/belgian-journal/resolve/main"

# 20 shards — UUID is constant across all shards
_SHARD_UUID = "5f2f13b8-33a7-435d-8d1c-caa7499681b2"
_NUM_SHARDS  = 20

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Dutch corporate/legal keywords for fast language heuristic
_NL_KEYWORDS = (
    "vennootschap", "naamloze", "besloten", "rechtspersoon", "oprichting",
    "maatschappij", "zetel", "aandeelhouder", "bestuurder", "zaakvoerder",
    "onderneming", "kapitaal", "statuten", "ontbinding", "fusie",
    "beperkte aansprakelijkheid", "burgerlijke vennootschap",
    " de ", " van ", " en ", " het ", " een ", " voor ",
)

_MIN_NL_HITS = 3  # require at least 3 keyword matches to count as Dutch


def _is_dutch(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    hits = sum(1 for kw in _NL_KEYWORDS if kw in lower)
    return hits >= _MIN_NL_HITS


def _fetch_shard(shard_idx: int) -> bytes:
    url = (
        f"{HF_BASE}/data/part-{shard_idx:05d}-{_SHARD_UUID}-c000.snappy.parquet"
    )
    headers = {
        "User-Agent": "NederCorpus/0.1 (academic research; CC0 open data)",
    }
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=120) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == 2:
                raise
            wait = 5.0 * (attempt + 1)
            logger.warning("[belgian_journal] Shard %d attempt %d failed: %s (retry in %.0fs)",
                           shard_idx, attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _log(output_dir: Path) -> None:
    entry = {
        "source": SOURCE,
        "url": f"{HF_BASE}/data/",
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(
    output_dir: Path,
    max_docs: int = 500_000,
    dry_run: bool = False,
) -> None:
    """
    Download and Dutch-filter all Belgian Journal Parquet shards.

    Each Dutch record is saved to belgian_journal_NNN.jsonl (one file per shard).
    Fields saved: pubid, company_name, publication_date, text, publication_link.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        logger.error("[belgian_journal] pyarrow not installed — run: pip install pyarrow")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[belgian_journal] [dry-run] would download %d shards from HuggingFace", _NUM_SHARDS)
        return

    total_dutch = 0
    total_rows  = 0

    for shard_idx in range(_NUM_SHARDS):
        out_file = output_dir / f"belgian_journal_{shard_idx:03d}.jsonl"
        if out_file.exists():
            # Count existing to resume correctly
            with out_file.open(encoding="utf-8") as f:
                n = sum(1 for _ in f)
            logger.info("[belgian_journal] Shard %d already done (%d Dutch records)", shard_idx, n)
            total_dutch += n
            if total_dutch >= max_docs:
                break
            continue

        logger.info("[belgian_journal] Downloading shard %d/%d…", shard_idx + 1, _NUM_SHARDS)
        try:
            raw = _fetch_shard(shard_idx)
        except Exception as exc:
            logger.warning("[belgian_journal] Shard %d download failed: %s", shard_idx, exc)
            continue

        try:
            tbl = pq.read_table(io.BytesIO(raw))
        except Exception as exc:
            logger.warning("[belgian_journal] Shard %d parse failed: %s", shard_idx, exc)
            continue

        rows = tbl.to_pydict()
        n_rows = len(rows.get("text", []))
        total_rows += n_rows
        dutch_count = 0

        with out_file.open("w", encoding="utf-8") as f:
            for i in range(n_rows):
                text = rows["text"][i] or ""
                if not _is_dutch(text):
                    continue

                pub_date = rows.get("publication_date", [None])[i]
                record = {
                    "pubid":            rows.get("pubid", [None])[i],
                    "company_name":     rows.get("company_name", [None])[i] or "",
                    "publication_date": str(pub_date) if pub_date else "",
                    "publication_link": rows.get("publication_link", [None])[i] or "",
                    "text":             text,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                dutch_count += 1
                total_dutch += 1

                if total_dutch >= max_docs:
                    break

        logger.info("[belgian_journal] Shard %d: %d/%d rows Dutch (total: %d)",
                    shard_idx, dutch_count, n_rows, total_dutch)
        _log(output_dir)

        if total_dutch >= max_docs:
            logger.info("[belgian_journal] Reached max_docs=%d, stopping.", max_docs)
            break

        time.sleep(1.0)

    logger.info("[belgian_journal] Done. %d Dutch records from %d total rows.",
                total_dutch, total_rows)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Belgian Journal Dutch bylaws (CC0)")
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=500_000)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
