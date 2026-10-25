"""
Download Dutch EU scientific texts from Pclanglais/EU-Science-Commons via Parquet shards.

EU-Science-Commons is a 28.2 GB multilingual corpus of EU-funded research
publications harvested by PleIAs from OpenAIRE, CORDIS, and EU institutional
repositories. All texts carry CC-BY or more permissive licenses.

Acquisition strategy (matches common_corpus_dutch_pd pattern):
  1. Query datasets-server.huggingface.co for Parquet shard URLs.
  2. Download each shard with pyarrow and write Dutch-filtered records as JSONL.
  3. Skip shards already done (resume-safe via done-shards file).

Dataset: https://huggingface.co/datasets/Pclanglais/EU-Science-Commons
Filter:  language in {"nl", "nld"}, language_confidence >= 0.85, no NC/ND licenses
License: CC-BY (per-record license field may vary; NC/ND filtered out)

Usage:
  python -m pipeline acquire --sources eu_science_commons
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

SOURCE     = "eu_science_commons"
LICENSE    = "CC-BY"
COUNTRY    = "EU"
DOMAIN     = "scientific"
HF_DATASET = "Pclanglais/EU-Science-Commons"
_UA        = "NederCorpus/0.1 (academic research; public data)"
_DELAY     = 0.5
_MIN_CONFIDENCE = 0.85
_DUTCH_LANGS    = {"nl", "nld", "dutch"}

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
            logger.warning("[eu_sc] HTTP %d for %s (attempt %d)", exc.code, url, attempt + 1)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[eu_sc] %s: %s (attempt %d)", url, exc, attempt + 1)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
    return None


def _list_parquet_shards() -> list[dict]:
    """Return list of {url, filename, split, size} dicts from datasets-server."""
    ds_url = f"https://datasets-server.huggingface.co/parquet?dataset={HF_DATASET}"
    req = urllib.request.Request(ds_url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
            resp = json.loads(r.read())
        shards = resp.get("parquet_files", [])
        if shards:
            logger.info("[eu_sc] datasets-server: %d total shards", len(shards))
            return shards
    except Exception as exc:
        logger.warning("[eu_sc] datasets-server failed: %s", exc)

    # Fallback: HF tree API
    logger.info("[eu_sc] Falling back to HF tree API")
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
            logger.warning("[eu_sc] Tree API error: %s", exc)

    logger.error("[eu_sc] Could not list shards for %s", HF_DATASET)
    return []


def _extract_dutch_records(raw: bytes) -> list[dict]:
    """Read a Parquet shard and return Dutch-language CC-compatible records."""
    try:
        import io
        import pyarrow.parquet as pq
        table = pq.read_table(io.BytesIO(raw))
    except Exception as exc:
        logger.warning("[eu_sc] Parquet read error: %s", exc)
        return []

    cols = table.schema.names
    text_col = next((c for c in ("text", "content", "body") if c in cols), None)
    if text_col is None:
        logger.warning("[eu_sc] No text column in: %s", cols)
        return []

    lang_col   = next((c for c in ("language", "lang") if c in cols), None)
    conf_col   = next((c for c in ("language_confidence", "confidence") if c in cols), None)
    url_col    = next((c for c in ("url", "source_url", "uri") if c in cols), None)
    year_col   = next((c for c in ("year", "date", "pub_year") if c in cols), None)
    title_col  = next((c for c in ("title",) if c in cols), None)
    src_col    = next((c for c in ("source_name", "source") if c in cols), None)
    lic_col    = next((c for c in ("license",) if c in cols), None)

    records = []
    for batch in table.to_batches(max_chunksize=2000):
        d = batch.to_pydict()
        n = len(d[text_col])
        for i in range(n):
            # Language filter
            if lang_col:
                lang = str(d[lang_col][i] or "").strip().lower()
                if lang not in _DUTCH_LANGS:
                    continue
            else:
                lang = "nl"

            # Confidence filter
            if conf_col:
                try:
                    conf = float(d[conf_col][i] or 0.0)
                except (TypeError, ValueError):
                    conf = 0.0
                if conf < _MIN_CONFIDENCE:
                    continue

            # License filter — exclude NC and ND
            rec_license = LICENSE
            if lic_col:
                rec_license = str(d[lic_col][i] or LICENSE).strip()
                if any(x in rec_license.upper() for x in ("NC", "ND")):
                    continue

            text = d[text_col][i]
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            text = (text or "").strip()
            if len(text) < 100:
                continue

            rec: dict = {"text": text, "license": rec_license}
            if url_col:
                rec["url"] = str(d[url_col][i] or "")
            if year_col:
                rec["year"] = str(d[year_col][i] or "")
            if title_col:
                rec["title"] = str(d[title_col][i] or "")
            if src_col:
                rec["source_name"] = str(d[src_col][i] or "")
            records.append(rec)
    return records


def download(
    output_dir: Path,
    max_docs: int = 200_000,
    dry_run: bool = False,
    hf_token: str | None = None,
    **_kwargs,
) -> None:
    """
    Download Pclanglais/EU-Science-Commons Dutch subset via Parquet shards.

    Records written to eu_science_commons_records.jsonl (append mode).
    Resume-safe via eu_science_commons_done_shards.txt.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[eu_sc] [dry-run] would download %s nl subset", HF_DATASET)
        return

    shards = _list_parquet_shards()
    if not shards:
        logger.error("[eu_sc] No shards found — dataset may be unavailable")
        (output_dir / "_NOT_FOUND.txt").write_text(
            f"datasets-server returned no shards for {HF_DATASET} on {acq_date}\n"
        )
        return

    logger.info("[eu_sc] %d total shards; will filter for Dutch", len(shards))

    out_path  = output_dir / "eu_science_commons_records.jsonl"
    done_file = output_dir / "eu_science_commons_done_shards.txt"
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
                logger.info("[eu_sc] Reached max_docs=%d, stopping.", max_docs)
                break

            shard_url  = shard.get("url", "")
            shard_name = shard.get("filename", "") or shard_url.split("/")[-1]

            if shard_name in done_shards:
                continue

            logger.info("[eu_sc] Downloading shard %s (%s B)", shard_name, shard.get("size", "?"))
            raw = _fetch_bytes(shard_url, timeout=300)
            if raw is None:
                logger.warning("[eu_sc] Failed: %s", shard_name)
                fdone.write(shard_name + "\n")
                fdone.flush()
                continue

            records = _extract_dutch_records(raw)
            logger.info("[eu_sc] %s: %d Dutch records", shard_name, len(records))

            for rec in records:
                entry = {
                    "text":             rec["text"],
                    "title":            rec.get("title", ""),
                    "url":              rec.get("url", ""),
                    "year":             rec.get("year", ""),
                    "source_name":      rec.get("source_name", ""),
                    "license":          rec.get("license", LICENSE),
                    "language":         "nl",
                    "source_dataset":   HF_DATASET,
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

    logger.info("[eu_sc] Done. %d Dutch documents written to %s", total_written, out_path)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Pclanglais/EU-Science-Commons Dutch subset (CC-BY) via Parquet"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=200_000)
    parser.add_argument("--hf-token", default=None)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, hf_token=args.hf_token, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
