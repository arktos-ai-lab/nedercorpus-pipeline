"""
Download the Common Corpus Dutch PD dataset from HuggingFace.

Rijgersberg/common_corpus_dutch_pd is a large collection of Dutch public-domain
texts compiled from the KB (Koninklijke Bibliotheek) Delpher digitisation project
and the Internet Archive — primarily books and periodicals pre-1928.

Dataset: https://huggingface.co/datasets/Rijgersberg/common_corpus_dutch_pd
License: Public Domain (CC0 / PD)
Expected size: ~18.6B words across hundreds of thousands of documents.

Acquisition strategy:
  1. Query datasets-server.huggingface.co for Parquet shard URLs.
  2. Download each shard with pyarrow and write records as JSONL.
  3. Skip shards already present (resume-safe).

Usage:
  python -m pipeline acquire --sources common_corpus_dutch_pd
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.request
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "common_corpus_dutch_pd"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "literature"

_DS_SERVER = "https://datasets-server.huggingface.co/parquet"
_DATASET   = "Rijgersberg/common_corpus_dutch_pd"
_UA        = "NederCorpus/0.1 (academic research; PD Dutch corpus)"
_DELAY     = 0.5

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _fetch_bytes(url: str, retries: int = 4, timeout: int = 120) -> bytes | None:
    headers = {"User-Agent": _UA}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            logger.warning("[cc_dutch_pd] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[cc_dutch_pd] %s: %s (attempt %d)", url, exc, attempt + 1)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
    return None


_HF_DATASET_API = "https://huggingface.co/api/datasets/{dataset}"
_HF_RESOLVE     = "https://huggingface.co/datasets/{dataset}/resolve/{branch}/{path}"


def _list_parquet_shards() -> list[dict]:
    """Return list of {url, filename, split, size} dicts.
    Tries datasets-server first, falls back to HF dataset API + tree listing.
    """
    # Primary: datasets-server
    url = f"{_DS_SERVER}?dataset={_DATASET}"
    data = _fetch_bytes(url, timeout=30)
    if data:
        try:
            resp = json.loads(data)
            shards = resp.get("parquet_files", [])
            if shards:
                return shards
        except json.JSONDecodeError:
            pass

    # Fallback: try the HuggingFace dataset tree API to list data/ files
    logger.info("[cc_dutch_pd] Falling back to HF tree API")
    tree_url = f"https://huggingface.co/api/datasets/{_DATASET}/tree/main/data"
    data = _fetch_bytes(tree_url, timeout=30)
    if data:
        try:
            tree = json.loads(data)
            shards = []
            for entry in (tree if isinstance(tree, list) else []):
                path = entry.get("path", "")
                if path.endswith(".parquet"):
                    dl_url = _HF_RESOLVE.format(
                        dataset=_DATASET, branch="main", path=path
                    )
                    shards.append({
                        "url":      dl_url,
                        "filename": path.split("/")[-1],
                        "split":    "train",
                        "size":     entry.get("size", 0),
                    })
            if shards:
                return shards
        except Exception as exc:
            logger.warning("[cc_dutch_pd] Tree API parse error: %s", exc)

    logger.error("[cc_dutch_pd] Could not list shards for %s", _DATASET)
    return []


def _extract_text_from_parquet(raw: bytes) -> list[dict]:
    """Read a Parquet shard (bytes) and return list of {id, text, year, source_url}."""
    try:
        import pyarrow.parquet as pq
        import io
        table = pq.read_table(io.BytesIO(raw))
    except Exception as exc:
        logger.warning("[cc_dutch_pd] Parquet read error: %s", exc)
        return []

    cols = table.schema.names
    logger.debug("[cc_dutch_pd] Parquet columns: %s", cols)

    # Detect text column (flexible naming)
    text_col = None
    for cand in ("text", "content", "body", "page_content", "passage"):
        if cand in cols:
            text_col = cand
            break
    if text_col is None:
        logger.warning("[cc_dutch_pd] No text column found in %s", cols)
        return []

    id_col   = "id" if "id" in cols else None
    url_col  = next((c for c in ("url", "source_url", "source", "uri") if c in cols), None)
    year_col = next((c for c in ("year", "date", "pub_year") if c in cols), None)
    title_col = next((c for c in ("title", "naam") if c in cols), None)

    records = []
    for i, batch in enumerate(table.to_batches(max_chunksize=1000)):
        d = batch.to_pydict()
        n = len(d[text_col])
        for j in range(n):
            text = d[text_col][j]
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            text = (text or "").strip()
            if len(text) < 50:
                continue
            rec = {"text": text}
            if id_col:
                rec["id"] = str(d[id_col][j] or "")
            if url_col:
                rec["source_url"] = str(d[url_col][j] or "")
            if year_col:
                val = d[year_col][j]
                rec["year"] = int(val) if val else None
            if title_col:
                rec["title"] = str(d[title_col][j] or "")
            records.append(rec)
    return records


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[cc_dutch_pd] [dry-run] would download %s", _DATASET)
        return

    shards = _list_parquet_shards()
    if not shards:
        logger.error("[cc_dutch_pd] No Parquet shards found — dataset may be private or unavailable")
        (output_dir / "_NOT_FOUND.txt").write_text(
            f"datasets-server returned no shards for {_DATASET} on {acq_date}\n"
        )
        return

    logger.info("[cc_dutch_pd] %d Parquet shards to download", len(shards))

    out_path  = output_dir / "cc_dutch_pd_records.jsonl"
    done_file = output_dir / "cc_dutch_pd_done_shards.txt"
    done_shards: set[str] = set()
    if done_file.exists():
        done_shards = {l.strip() for l in done_file.read_text().splitlines() if l.strip()}

    total_written = sum(1 for _ in out_path.open(encoding="utf-8", errors="replace")) if out_path.exists() else 0

    with out_path.open("a", encoding="utf-8") as fout, done_file.open("a", encoding="utf-8") as fdone:
        for shard in shards:
            shard_url  = shard.get("url", "")
            shard_name = shard.get("filename", "") or shard_url.split("/")[-1]
            split      = shard.get("split", "train")

            if shard_name in done_shards:
                logger.info("[cc_dutch_pd] Skipping already-done shard: %s", shard_name)
                continue

            logger.info("[cc_dutch_pd] Downloading shard %s (%s bytes)", shard_name, shard.get("size", "?"))
            raw = _fetch_bytes(shard_url, retries=4, timeout=300)
            if raw is None:
                logger.warning("[cc_dutch_pd] Failed to download shard %s", shard_name)
                continue

            records = _extract_text_from_parquet(raw)
            logger.info("[cc_dutch_pd] Shard %s: %d usable records", shard_name, len(records))

            for rec in records:
                entry = {
                    "source":           SOURCE,
                    "text":             rec["text"],
                    "title":            rec.get("title", ""),
                    "id":               rec.get("id", ""),
                    "source_url":       rec.get("source_url", ""),
                    "year":             rec.get("year"),
                    "license":          LICENSE,
                    "acquisition_date": acq_date,
                    "split":            split,
                }
                fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
                total_written += 1

            fdone.write(shard_name + "\n")
            fdone.flush()
            time.sleep(_DELAY)

    logger.info("[cc_dutch_pd] Done. %d total records in %s", total_written, out_path)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": f"https://huggingface.co/datasets/{_DATASET}",
            "license": LICENSE, "acquisition_date": acq_date,
            "total_documents": total_written,
        }, ensure_ascii=False) + "\n")
