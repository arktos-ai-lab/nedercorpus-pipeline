"""
extract_gptnl_full.py — Full, exact extraction of ALL Dutch + Frisian records from the
GPT-NL Public Corpus (no reservoir sampling, no config skipped except genuinely
non-Dutch-bearing ones).

Unlike sample_gptnl.py (which reservoir-sampled to an 8B/36B token *budget*), this
script writes every matching record and reports the exact token total — needed
because the earlier budgeted run silently produced two byte-identical files
(gptnl_8b.jsonl == gptnl_36b.jsonl), which was a bug.

Fetches shards directly via urllib (datasets-server for the shard list, then plain
HTTPS GETs), NOT the `datasets` streaming library — on this network, both `datasets`
streaming AND raw urllib hit frequent TLS-handshake resets against huggingface.co's
CDN, so retries are done at the SHARD level (one ~100-300MB file) rather than
restarting an entire config's stream from row 0. This means a flaky connection only
costs a shard retry, never re-processing (or duplicating) rows already written.

Configs skipped entirely (verified non-Dutch/Frisian regardless of the `language`
column — English, German, code, or English-dominant multilingual academic text):
  american-stories, cc_english-pd, cc_german-pd, cc_github_open_source,
  cc_loc-pd-books, cc_openalex

Usage:
  pip install pyarrow tiktoken
  python extract_gptnl_full.py
  python extract_gptnl_full.py --resume   # skip shards already recorded done
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import tiktoken

OUT_DIR = Path(__file__).resolve().parent / "tier_splits"
DATASET = "GPT-NL/GPT-NL_Public_Corpus"
TIKTOKEN_ENCODING = "o200k_base"
_DS_SERVER = f"https://datasets-server.huggingface.co/parquet?dataset={DATASET}"
_UA = "NederCorpus/0.1 (academic; Dutch+Frisian token-count comparison; CC-BY-4.0 source)"

_DUTCH_FRISIAN = {"nl", "nld", "dutch", "fy", "fry", "fries", "frisian"}

# Genuinely non-Dutch/Frisian regardless of any mislabeled rows — skip for speed.
_SKIP_CONFIGS = frozenset({
    "american-stories",
    "cc_english-pd",
    "cc_german-pd",
    "cc_github_open_source",
    "cc_loc-pd-books",
    "cc_openalex",
})

def approx_tokens(text: str) -> int:
    return len(text.split())


def is_dutch_or_frisian(record: dict) -> bool:
    lang = (record.get("language") or "").lower().strip()
    return lang in _DUTCH_FRISIAN


_CURL = r"C:\Windows\System32\curl.exe"


def _fetch(url: str, retries: int = 8, timeout: int = 90) -> bytes | None:
    """Fetch via curl.exe in a subprocess with a hard wall-clock kill.

    Python's urllib/ssl on this machine has been observed to hang well past
    its configured socket timeout on a stalled TLS handshake (a known Windows
    edge case) — a plain retry loop around urlopen() can silently wedge for
    30+ minutes on one bad connection. Shelling out to curl.exe and enforcing
    our own subprocess.run(timeout=...) guarantees the attempt is killed at
    the OS process level no matter what curl's internals are doing.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        for attempt in range(retries):
            try:
                proc = subprocess.run(
                    [
                        _CURL, "-s", "-S", "-k", "-L",
                        "--max-time", str(timeout),
                        "--connect-timeout", "20",
                        "-A", _UA,
                        "-o", tmp_path,
                        "-w", "%{http_code}",
                        url,
                    ],
                    capture_output=True, text=True, timeout=timeout + 15,
                )
                code = (proc.stdout or "").strip()
                if code == "200" and os.path.getsize(tmp_path) > 0:
                    with open(tmp_path, "rb") as f:
                        return f.read()
                if code in ("404", "410"):
                    print(f"    HTTP {code} (permanent): {url[-60:]}", flush=True)
                    return None
                print(f"    Attempt {attempt+1}/{retries} curl exit={proc.returncode} "
                      f"http={code!r} stderr={proc.stderr.strip()[:200]!r}: {url[-60:]}", flush=True)
            except subprocess.TimeoutExpired:
                print(f"    Attempt {attempt+1}/{retries} hard-timeout after {timeout+15}s: {url[-60:]}", flush=True)
            except Exception as exc:
                print(f"    Attempt {attempt+1}/{retries} error ({exc}): {url[-60:]}", flush=True)
            if attempt < retries - 1:
                time.sleep(min(30, 5 * (attempt + 1)))
        return None
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def list_shards() -> dict[str, list[dict]]:
    data = _fetch(_DS_SERVER, retries=5, timeout=30)
    if not data:
        sys.exit("ERROR: could not list shards from datasets-server")
    resp = json.loads(data)
    by_config: dict[str, list] = {}
    for s in resp.get("parquet_files", []):
        by_config.setdefault(s.get("config", ""), []).append(s)
    return by_config


def records_from_parquet(raw: bytes, cfg: str) -> list[dict]:
    import pyarrow.parquet as pq
    try:
        table = pq.read_table(io.BytesIO(raw))
    except Exception as exc:
        print(f"    Parquet parse error for {cfg}: {exc}", flush=True)
        return []
    d = table.to_pydict()
    n = len(d.get("text", []))
    out = []
    for i in range(n):
        out.append({
            "text": d["text"][i],
            "language": d.get("language", [None] * n)[i],
            "license": d.get("license", [None] * n)[i],
            "dataset_url": d.get("dataset_url", [None] * n)[i],
            "title": d.get("title", [None] * n)[i],
            "dataset_name": d.get("dataset_name", [None] * n)[i],
            "id": d.get("id", [None] * n)[i],
        })
    return out


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--out_dir", type=Path, default=OUT_DIR)
    p.add_argument("--resume", action="store_true",
                   help="Skip shards already recorded in done_shards.json")
    p.add_argument("--only_configs", nargs="*", default=None,
                   help="Process only these config names")
    return p.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / "gptnl_all_nl_fy.jsonl"
    progress_path = args.out_dir / "extract_gptnl_full_progress.json"
    summary_path = args.out_dir / "extract_gptnl_full_summary.json"

    enc = tiktoken.get_encoding(TIKTOKEN_ENCODING)

    done_shards: set[str] = set()
    failed_shards: list[str] = []
    tokens_by_cfg: dict[str, int] = defaultdict(int)
    rows_by_cfg: dict[str, int] = defaultdict(int)
    tokens_by_lang: dict[str, int] = defaultdict(int)
    rows_by_lang: dict[str, int] = defaultdict(int)
    tiktoken_by_cfg: dict[str, int] = defaultdict(int)
    tiktoken_by_lang: dict[str, int] = defaultdict(int)

    mode = "a"
    if args.resume and progress_path.exists():
        prog = json.loads(progress_path.read_text())
        done_shards = set(prog.get("done_shards", []))
        failed_shards = prog.get("failed_shards", [])
        tokens_by_cfg.update(prog.get("tokens_by_cfg", {}))
        rows_by_cfg.update(prog.get("rows_by_cfg", {}))
        tokens_by_lang.update(prog.get("tokens_by_lang", {}))
        rows_by_lang.update(prog.get("rows_by_lang", {}))
        tiktoken_by_cfg.update(prog.get("tiktoken_by_cfg", {}))
        tiktoken_by_lang.update(prog.get("tiktoken_by_lang", {}))
        print(f"Resuming — {len(done_shards)} shards already done", flush=True)
    else:
        mode = "w"

    print(f"Listing shards for {DATASET} via datasets-server ...", flush=True)
    all_shards = list_shards()
    configs = sorted(all_shards.keys())
    if args.only_configs:
        configs = [c for c in configs if c in args.only_configs]
    print(f"Found {len(configs)} configs total", flush=True)

    def checkpoint():
        progress_path.write_text(json.dumps({
            "done_shards": sorted(done_shards),
            "failed_shards": failed_shards,
            "tokens_by_cfg": dict(tokens_by_cfg),
            "rows_by_cfg": dict(rows_by_cfg),
            "tokens_by_lang": dict(tokens_by_lang),
            "rows_by_lang": dict(rows_by_lang),
            "tiktoken_by_cfg": dict(tiktoken_by_cfg),
            "tiktoken_by_lang": dict(tiktoken_by_lang),
            "tiktoken_encoding": TIKTOKEN_ENCODING,
        }, ensure_ascii=False, indent=2))

    total_written = sum(rows_by_cfg.values())

    with open(out_path, mode, encoding="utf-8") as fout:
        for cfg in configs:
            if cfg in _SKIP_CONFIGS:
                print(f"[{cfg}] skipping (known non-Dutch/Frisian)", flush=True)
                continue
            shards = all_shards[cfg]
            print(f"[{cfg}] {len(shards)} shards", flush=True)
            for shard in shards:
                shard_url = shard.get("url", "")
                shard_key = f"{cfg}/{shard_url.split('/')[-1]}#{shard.get('filename', shard_url)}"
                shard_key = f"{cfg}::{shard_url}"
                if shard_key in done_shards:
                    continue

                raw = _fetch(shard_url, retries=8, timeout=90)
                if raw is None:
                    print(f"  [{cfg}] FAILED shard permanently: {shard_url[-60:]}", flush=True)
                    failed_shards.append(shard_key)
                    done_shards.add(shard_key)  # don't retry forever on --resume
                    checkpoint()
                    continue

                records = records_from_parquet(raw, cfg)
                matched = [r for r in records if is_dutch_or_frisian(r)]

                docs, ns = [], []
                for r in matched:
                    text = (r.get("text") or "").strip()
                    if not text:
                        continue
                    n = approx_tokens(text)
                    if n < 10:
                        continue
                    lang = (r.get("language") or "").lower().strip()
                    docs.append({
                        "text": text,
                        "meta": {
                            "source": r.get("dataset_name") or cfg,
                            "config": cfg,
                            "language": lang,
                            "language_variant": "FY" if lang.startswith(("fy", "fr")) else "NL",
                            "license": r.get("license") or "unknown",
                            "dataset_url": r.get("dataset_url") or "",
                            "title": r.get("title") or "",
                        },
                    })
                    ns.append(n)

                tik_counts = enc.encode_ordinary_batch(
                    [d["text"] for d in docs], num_threads=8
                ) if docs else []

                for doc, n, tik in zip(docs, ns, tik_counts):
                    fout.write(json.dumps(doc, ensure_ascii=False) + "\n")
                    total_written += 1
                    lang = doc["meta"]["language"]
                    tokens_by_cfg[cfg] += n
                    rows_by_cfg[cfg] += 1
                    tokens_by_lang[lang] += n
                    rows_by_lang[lang] += 1
                    tiktoken_by_cfg[cfg] += len(tik)
                    tiktoken_by_lang[lang] += len(tik)
                fout.flush()

                done_shards.add(shard_key)
                checkpoint()
                print(f"  [{cfg}] shard done: {len(records):,} rows -> {len(docs):,} nl/fy kept | "
                      f"running total {sum(tokens_by_cfg.values())/1e9:.3f}B whitespace / "
                      f"{sum(tiktoken_by_cfg.values())/1e9:.3f}B {TIKTOKEN_ENCODING}", flush=True)

            print(f"[{cfg}] CONFIG DONE — {rows_by_cfg.get(cfg,0):,} rows, "
                  f"{tokens_by_cfg.get(cfg,0)/1e9:.3f}B whitespace, "
                  f"{tiktoken_by_cfg.get(cfg,0)/1e9:.3f}B {TIKTOKEN_ENCODING}", flush=True)

    total_tokens = sum(tokens_by_cfg.values())
    total_tiktoken = sum(tiktoken_by_cfg.values())
    summary = {
        "total_docs": total_written,
        "failed_shards": failed_shards,
        "total_tokens_whitespace_billions": total_tokens / 1e9,
        "total_tokens_tiktoken_billions": total_tiktoken / 1e9,
        "tiktoken_encoding": TIKTOKEN_ENCODING,
        "tokens_by_config": dict(sorted(tokens_by_cfg.items(), key=lambda x: -x[1])),
        "tiktoken_by_config": dict(sorted(tiktoken_by_cfg.items(), key=lambda x: -x[1])),
        "rows_by_config": dict(rows_by_cfg),
        "tokens_by_language": dict(sorted(tokens_by_lang.items(), key=lambda x: -x[1])),
        "tiktoken_by_language": dict(sorted(tiktoken_by_lang.items(), key=lambda x: -x[1])),
        "rows_by_language": dict(rows_by_lang),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print("\n=== FINAL ===", flush=True)
    print(f"Total docs written: {total_written:,}", flush=True)
    if failed_shards:
        print(f"WARNING — {len(failed_shards)} shards permanently failed (network): {failed_shards}", flush=True)
    print(f"Total tokens (whitespace): {total_tokens/1e9:.3f}B", flush=True)
    print(f"Total tokens ({TIKTOKEN_ENCODING}): {total_tiktoken/1e9:.3f}B", flush=True)
    print(f"Output: {out_path}", flush=True)
    print(f"Summary: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
