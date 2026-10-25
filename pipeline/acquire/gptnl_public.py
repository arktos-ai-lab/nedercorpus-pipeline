"""
EXCLUDED: GPT-NL Public Corpus — sourcing from intermediary violates NederCorpus policy.
Policy: all sources must be acquired from the original provider, not from another model's
training corpus. The subsets below were investigated for direct acquisition:
  - utrechts-archief: HUA OAI-PMH firewalled outside NL (inaccessible)
  - naturalis: OAI-PMH returns only deleted records (repository migrated)
  - dpc/rijksoverheid: government web texts, largely covered by existing NL gov sources
  - zeeuws-archief: only WP blog posts via sitemap, not the 33K archival records
  - wikiwijs: kennisnet.nl SRU API DNS dead, no OAI-PMH (inaccessible)
  - noordhollandsarchief: website times out (inaccessible)
  - dansknaw: no DANS set filter for KNAW institution (would require full harvest)
  - auditdienstrijk: sitemap broken (500 error, inaccessible)
Raw data retained in data/raw/gptnl_public/ for reference only.

Original docstring (historical):
Download Dutch-relevant subsets from the GPT-NL Public Corpus (HuggingFace).

GPT-NL/GPT-NL_Public_Corpus is the training corpus for the Dutch GPT-NL model,
published under CC-BY-4.0 by the Netherlands eScience Center / TNO / SURF.

We only download subsets NOT already covered by existing NederCorpus sources:
  utrechts-archief  — Utrecht regional archive docs      (412 MB, CC-BY)
  naturalis         — Naturalis biodiversity institute   (292 MB, CC-BY)
  dpc               — Dutch Parallel Corpus subset       (197 MB, CC-BY)
  zeeuws-archief    — Zeeland regional archive           ( 78 MB, CC-BY)
  wikiwijs          — Kennisnet Wikiwijs learning mats.  ( 64 MB, CC-BY)
  noordhollandsarchief — Noord-Holland archive           ( 51 MB, CC-BY)
  dansknaw          — DANS-KNAW research datasets        ( 50 MB, CC-BY)
  auditdienstrijk   — Audit service documents            ( 10 MB, PD)

Subsets explicitly excluded (already in NederCorpus or non-Dutch):
  belgian-journal, cc_eurovoc, european-parliament, kb, kb-open-kranten,
  kb-pd-books, multi-eurlex, nationaal-archief, officiele-bekendmakingen,
  openraadsinformatie-*, rechtspraak, tweedekamer, wikidata, woogle, pbl
  american-stories (EN), cc_english-pd, cc_german-pd, cc_github_open_source,
  cc_loc-pd-books, cc_openalex, common-crawl (excl. web crawl policy),
  youtube-commons (ToS)

License: CC-BY-4.0  (individual records may note original PD/CC-0 license)
URL: https://huggingface.co/datasets/GPT-NL/GPT-NL_Public_Corpus

Usage:
  python -m pipeline acquire --sources gptnl_public
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "gptnl_public"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "mixed"

_DATASET   = "GPT-NL/GPT-NL_Public_Corpus"
_DS_SERVER = f"https://datasets-server.huggingface.co/parquet?dataset={_DATASET}"
_HF_RESOLVE = "https://huggingface.co/datasets/{dataset}/resolve/{branch}/{path}"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; GPT-NL Public Corpus CC-BY-4.0)"
_DELAY     = 1.0

# Subsets to download: (config_name, subdomain, label)
_SUBSETS = [
    ("utrechts-archief",     "utrechts_archief",      "Utrecht Archief"),
    ("naturalis",            "naturalis",              "Naturalis Biodiversity Institute"),
    ("dpc",                  "dpc",                    "Dutch Parallel Corpus"),
    ("zeeuws-archief",       "zeeuws_archief",         "Zeeuws Archief"),
    ("wikiwijs",             "wikiwijs_gptnl",         "Wikiwijs (via GPT-NL)"),
    ("noordhollandsarchief", "noordhollands_archief",  "Noord-Hollands Archief"),
    ("dansknaw",             "dansknaw",               "DANS-KNAW Research Data"),
    ("auditdienstrijk",      "auditdienstrijk",        "Auditdienst Rijk"),
]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _fetch(url: str, retries: int = 4, timeout: int = 300) -> bytes | None:
    headers = {"User-Agent": _UA}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                logger.warning("[gptnl] HTTP %d: %s", exc.code, url[:80])
                return None
            logger.warning("[gptnl] HTTP %d attempt %d: %s", exc.code, attempt+1, url[:80])
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[gptnl] Attempt %d error %s: %s", attempt+1, url[:80], exc)
            if attempt < retries - 1:
                time.sleep(5.0 * (attempt + 1))
    return None


def _list_shards() -> dict[str, list[dict]]:
    """Return {config_name: [shard_dict, ...]} from datasets-server."""
    data = _fetch(_DS_SERVER, timeout=30)
    if data:
        try:
            resp = json.loads(data)
            shards = resp.get("parquet_files", [])
            if shards:
                by_config: dict[str, list] = {}
                for s in shards:
                    cfg = s.get("config", "")
                    by_config.setdefault(cfg, []).append(s)
                return by_config
        except Exception as exc:
            logger.warning("[gptnl] datasets-server parse error: %s", exc)

    # Fallback: HF tree API
    logger.info("[gptnl] Falling back to HF tree API")
    by_config: dict[str, list] = {}
    for cfg, subdir, _ in _SUBSETS:
        tree_url = f"https://huggingface.co/api/datasets/{_DATASET}/tree/main/{cfg}"
        data = _fetch(tree_url, timeout=30)
        if not data:
            continue
        try:
            tree = json.loads(data)
            for entry in (tree if isinstance(tree, list) else []):
                path = entry.get("path", "")
                if path.endswith(".parquet"):
                    dl_url = _HF_RESOLVE.format(
                        dataset=_DATASET, branch="main", path=path
                    )
                    by_config.setdefault(cfg, []).append({
                        "url":    dl_url,
                        "config": cfg,
                        "size":   entry.get("size", 0),
                    })
        except Exception as exc:
            logger.warning("[gptnl] tree API parse error for %s: %s", cfg, exc)

    return by_config


def _records_from_parquet(raw: bytes, cfg: str) -> list[dict]:
    """Extract text records from a Parquet shard."""
    try:
        import pyarrow.parquet as pq
        table = pq.read_table(io.BytesIO(raw))
    except Exception as exc:
        logger.warning("[gptnl] Parquet read error for %s: %s", cfg, exc)
        return []

    cols = table.schema.names
    if "text" not in cols:
        logger.warning("[gptnl] No 'text' column in %s (got %s)", cfg, cols)
        return []

    d = table.to_pydict()
    records = []
    for i in range(len(d["text"])):
        text = d["text"][i]
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        text = (text or "").strip()
        if len(text) < 50:
            continue
        lang = (d.get("language", ["nl"] * len(d["text"]))[i] or "nl").lower()
        # Only keep Dutch / Frisian / Afrikaans
        if lang not in ("nl", "nld", "dutch", "fy", "fry", "af", "afr", ""):
            continue
        lic  = (d.get("license", ["CC-BY"] * len(d["text"]))[i] or "CC-BY")
        # Reject NC/ND licenses
        lic_low = lic.lower()
        if "nc" in lic_low or "nd" in lic_low:
            continue
        records.append({
            "text":        text,
            "title":       str(d.get("title", [""]*len(d["text"]))[i] or ""),
            "id":          str(d.get("id",    [""]*len(d["text"]))[i] or ""),
            "source_url":  str(d.get("dataset_url", [""]*len(d["text"]))[i] or ""),
            "license":     lic,
            "language":    lang,
        })
    return records


def download(output_dir: Path, dry_run: bool = False) -> None:
    raise RuntimeError(
        "gptnl_public is EXCLUDED: NederCorpus policy requires sourcing from the original "
        "provider, not from an intermediary training corpus. Each subset was investigated "
        "for direct acquisition; see module docstring for findings. "
        "Raw data retained in data/raw/gptnl_public/ for reference only."
    )

    logger.info("[gptnl] Listing shards from datasets-server...")
    all_shards = _list_shards()

    want_configs = {cfg for cfg, _, _ in _SUBSETS}
    available = {c: v for c, v in all_shards.items() if c in want_configs}
    logger.info("[gptnl] Found shards for: %s", sorted(available.keys()))

    total_written = 0
    for cfg, subdir, label in _SUBSETS:
        shards = available.get(cfg, [])
        if not shards:
            logger.warning("[gptnl] No shards for %s — skipping", cfg)
            continue

        sub_dir = output_dir / subdir
        sub_dir.mkdir(parents=True, exist_ok=True)
        out_path  = sub_dir / f"{subdir}.jsonl"
        done_file = sub_dir / "done_shards.txt"
        done: set[str] = set()
        if done_file.exists():
            done = {l.strip() for l in done_file.read_text().splitlines() if l.strip()}

        logger.info("[gptnl] %s: %d shards", label, len(shards))

        with out_path.open("a", encoding="utf-8") as fout, \
             done_file.open("a", encoding="utf-8") as fdone:
            for shard in shards:
                shard_url  = shard.get("url", "")
                shard_name = shard_url.split("/")[-1]
                if shard_name in done:
                    logger.info("[gptnl] %s: skip %s", label, shard_name)
                    continue
                size_mb = shard.get("size", 0) // 1024 // 1024
                logger.info("[gptnl] %s: downloading %s (%dMB)", label, shard_name, size_mb)

                raw = _fetch(shard_url, retries=4, timeout=600)
                if raw is None:
                    logger.warning("[gptnl] %s: failed to download %s", label, shard_name)
                    continue

                records = _records_from_parquet(raw, cfg)
                logger.info("[gptnl] %s: %d Dutch records from %s", label, len(records), shard_name)

                for rec in records:
                    entry = {
                        "source":           SOURCE,
                        "subset":           cfg,
                        "text":             rec["text"],
                        "title":            rec["title"],
                        "id":               rec["id"],
                        "source_url":       rec["source_url"],
                        "license":          rec["license"],
                        "language":         rec["language"],
                        "acquisition_date": acq_date,
                    }
                    fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    total_written += 1
                fout.flush()

                fdone.write(shard_name + "\n")
                fdone.flush()
                time.sleep(_DELAY)

        logger.info("[gptnl] %s done", label)

    logger.info("[gptnl] Total: %d records across all subsets", total_written)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": f"https://huggingface.co/datasets/{_DATASET}",
            "license": LICENSE, "acquisition_date": acq_date,
            "total_documents": total_written,
        }, ensure_ascii=False) + "\n")
