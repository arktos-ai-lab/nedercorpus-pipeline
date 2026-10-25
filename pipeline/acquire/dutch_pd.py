"""
Download Dutch public-domain texts from PleIAs/common_corpus via Parquet shards.

PleIAs/Common Corpus is a large multilingual public-domain corpus (4.49 TB)
compiled by PleIAs from OpenCulture, OpenGovernment, and OpenScience collections.
Dutch is one of 33+ language subsets, with well over 1B tokens.

Acquisition strategy (matches common_corpus_dutch_pd pattern):
  1. Query datasets-server.huggingface.co for Parquet shard URLs.
  2. Download each shard with pyarrow and write Dutch-filtered records as JSONL.
  3. Skip shards already done (resume-safe via done-shards file).

Dataset: https://huggingface.co/datasets/PleIAs/common_corpus
Filter:  language == "Dutch"
License: Public Domain / CC0

Note: common_corpus_dutch_pd covers Rijgersberg/common_corpus_dutch_pd (KB Delpher).
      This acquirer targets the PleIAs build — different curation, minimal overlap.

Usage:
  python -m pipeline acquire --sources dutch_pd
"""
from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE     = "dutch_pd"
LICENSE    = "PD"
COUNTRY    = "NL"
DOMAIN     = "literature"
HF_DATASET = "PleIAs/common_corpus"
_UA        = "NederCorpus/0.1 (academic research; public data)"
_DELAY     = 0.5

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _fetch_bytes(url: str, retries: int = 4, timeout: int = 300) -> bytes | None:
    headers = {"User-Agent": _UA}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            logger.warning("[dutch_pd] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[dutch_pd] %s: %s (attempt %d)", url, exc, attempt + 1)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
    return None


def _list_parquet_shards(hf_token: str | None = None) -> list[dict]:
    """Return list of {url, filename, split, size} dicts from datasets-server."""
    ds_url = f"https://datasets-server.huggingface.co/parquet?dataset={HF_DATASET}"
    headers = {"User-Agent": _UA}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    req = urllib.request.Request(ds_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
            resp = json.loads(r.read())
        shards = resp.get("parquet_files", [])
        if shards:
            logger.info("[dutch_pd] datasets-server: %d total shards", len(shards))
            return shards
    except Exception as exc:
        logger.warning("[dutch_pd] datasets-server failed: %s", exc)

    # Fallback: HF tree API
    logger.info("[dutch_pd] Falling back to HF tree API")
    tree_url = f"https://huggingface.co/api/datasets/{HF_DATASET}/tree/main/data"
    data = _fetch_bytes(tree_url, timeout=30)
    if data:
        try:
            tree = json.loads(data)
            shards = []
            for entry in (tree if isinstance(tree, list) else []):
                path = entry.get("path", "")
                if path.endswith(".parquet"):
                    dl_url = (
                        f"https://huggingface.co/datasets/{HF_DATASET}"
                        f"/resolve/main/{path}"
                    )
                    shards.append({
                        "url": dl_url,
                        "filename": path.split("/")[-1],
                        "split": "train",
                        "size": entry.get("size", 0),
                    })
            if shards:
                return shards
        except Exception as exc:
            logger.warning("[dutch_pd] Tree API error: %s", exc)

    logger.error("[dutch_pd] Could not list shards for %s", HF_DATASET)
    return []


def _extract_dutch_records(raw: bytes) -> list[dict]:
    """Read a Parquet shard and return Dutch-language records."""
    try:
        import io
        import pyarrow.parquet as pq
        table = pq.read_table(io.BytesIO(raw))
    except Exception as exc:
        logger.warning("[dutch_pd] Parquet read error: %s", exc)
        return []

    cols = table.schema.names
    text_col = next((c for c in ("text", "content", "body") if c in cols), None)
    if text_col is None:
        logger.warning("[dutch_pd] No text column in: %s", cols)
        return []

    lang_col  = next((c for c in ("language", "lang") if c in cols), None)
    url_col   = next((c for c in ("url", "source_url", "source", "uri") if c in cols), None)
    date_col  = next((c for c in ("date", "year", "pub_year") if c in cols), None)
    title_col = next((c for c in ("title", "naam") if c in cols), None)

    records = []
    for batch in table.to_batches(max_chunksize=2000):
        d = batch.to_pydict()
        n = len(d[text_col])
        for i in range(n):
            # Language filter
            if lang_col:
                lang = str(d[lang_col][i] or "").strip()
                if lang.lower() not in ("dutch", "nl", "nld"):
                    continue

            text = d[text_col][i]
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            text = (text or "").strip()
            if len(text) < 200:
                continue

            rec: dict = {"text": text}
            if url_col:
                rec["url"] = str(d[url_col][i] or "")
            if date_col:
                rec["date"] = str(d[date_col][i] or "")
            if title_col:
                rec["title"] = str(d[title_col][i] or "")
            records.append(rec)
    return records


def download(
    output_dir: Path,
    max_docs: int = 500_000,
    dry_run: bool = False,
    hf_token: str | None = None,
    **_kwargs,
) -> None:
    """
    Download PleIAs/common_corpus Dutch subset via Parquet shards.

    Records written to dutch_pd_records.jsonl (append mode).
    Resume-safe via dutch_pd_done_shards.txt.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[dutch_pd] [dry-run] would download %s Dutch subset", HF_DATASET)
        return

    shards = _list_parquet_shards(hf_token=hf_token)
    if not shards:
        logger.error("[dutch_pd] No shards found — dataset may be unavailable")
        (output_dir / "_NOT_FOUND.txt").write_text(
            f"datasets-server returned no shards for {HF_DATASET} on {acq_date}\n"
        )
        return

    logger.info("[dutch_pd] %d total shards; will filter for Dutch", len(shards))

    out_path  = output_dir / "dutch_pd_records.jsonl"
    done_file = output_dir / "dutch_pd_done_shards.txt"
    done_shards: set[str] = set()
    if done_file.exists():
        done_shards = {l.strip() for l in done_file.read_text().splitlines() if l.strip()}

    total_written = 0
    if out_path.exists():
        with out_path.open(encoding="utf-8", errors="replace") as f:
            for _ in f:
                total_written += 1

    with out_path.open("a", encoding="utf-8") as fout, done_file.open("a") as fdone:
        for shard in shards:
            if total_written >= max_docs:
                logger.info("[dutch_pd] Reached max_docs=%d, stopping.", max_docs)
                break

            shard_url  = shard.get("url", "")
            shard_name = shard.get("filename", "") or shard_url.split("/")[-1]

            if shard_name in done_shards:
                continue

            logger.info("[dutch_pd] Downloading shard %s (%s B)", shard_name, shard.get("size", "?"))
            raw = _fetch_bytes(shard_url, timeout=300)
            if raw is None:
                logger.warning("[dutch_pd] Failed: %s", shard_name)
                fdone.write(shard_name + "\n")
                fdone.flush()
                continue

            records = _extract_dutch_records(raw)
            logger.info("[dutch_pd] %s: %d Dutch records", shard_name, len(records))

            for rec in records:
                entry = {
                    "text":             rec["text"],
                    "title":            rec.get("title", ""),
                    "date":             rec.get("date", ""),
                    "url":              rec.get("url", ""),
                    "source_dataset":   HF_DATASET,
                    "license":          LICENSE,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
                total_written += 1
                if total_written >= max_docs:
                    break

            fdone.write(shard_name + "\n")
            fdone.flush()
            fout.flush()
            time.sleep(_DELAY)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": f"https://huggingface.co/datasets/{HF_DATASET}",
            "license": LICENSE, "acquisition_date": acq_date,
            "total_documents": total_written,
        }, ensure_ascii=False) + "\n")

    logger.info("[dutch_pd] Done. %d Dutch documents written to %s", total_written, out_path)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download PleIAs/common_corpus Dutch subset (PD) via Parquet"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=500_000)
    parser.add_argument("--hf-token", default=None)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, hf_token=args.hf_token, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
