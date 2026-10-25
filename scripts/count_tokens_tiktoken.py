"""
count_tokens_tiktoken.py — Real subword-tokenizer count of the NederCorpus final export.

pipeline/stats.py reports "tokens" as a whitespace-split approximation (see its
module docstring). That number is not comparable to token counts published in
LLM-pretraining papers (e.g. GPT-NL's 36B figure), which are real BPE/tokenizer
tokens. This script re-counts data/final/*.parquet with tiktoken for an
apples-to-apples comparison.

Usage:
  pip install tiktoken pyarrow
  python scripts/count_tokens_tiktoken.py --encoding o200k_base
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
import tiktoken

REPO_ROOT = Path(__file__).resolve().parent.parent
FINAL_DIR = REPO_ROOT / "data" / "final"
BATCH_ROWS = 5000


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--final_dir", type=Path, default=FINAL_DIR)
    p.add_argument("--encoding", default="o200k_base")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "tiktoken_corpus_stats.json")
    return p.parse_args()


def main():
    args = parse_args()
    enc = tiktoken.get_encoding(args.encoding)

    files = sorted(glob.glob(str(args.final_dir / "*.parquet")))
    if not files:
        raise SystemExit(f"No parquet files found in {args.final_dir}")
    print(f"Found {len(files)} shards in {args.final_dir}", flush=True)

    total_docs = 0
    total_tokens = 0
    total_chars = 0
    by_source: dict[str, int] = defaultdict(int)
    by_domain: dict[str, int] = defaultdict(int)
    by_license: dict[str, int] = defaultdict(int)
    by_country: dict[str, int] = defaultdict(int)
    by_lang_variant: dict[str, int] = defaultdict(int)

    for fi, path in enumerate(files):
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(
            batch_size=BATCH_ROWS,
            columns=["text", "source", "domain", "license", "country", "language_variant"],
        ):
            cols = batch.to_pydict()
            texts = cols["text"]
            n_tok_list = enc.encode_ordinary_batch(texts, num_threads=8)
            for i, text in enumerate(texts):
                n = len(n_tok_list[i])
                total_docs += 1
                total_tokens += n
                total_chars += len(text) if text else 0
                by_source[cols["source"][i] or "unknown"] += n
                by_domain[cols["domain"][i] or "unknown"] += n
                by_license[cols["license"][i] or "unknown"] += n
                by_country[cols["country"][i] or "unknown"] += n
                by_lang_variant[cols["language_variant"][i] or "unknown"] += n

        print(f"  [{fi+1}/{len(files)}] {Path(path).name}: "
              f"running total {total_docs:,} docs, {total_tokens/1e9:.3f}B tokens", flush=True)

        # checkpoint after every shard so a crash doesn't lose all progress
        result = {
            "encoding": args.encoding,
            "shards_done": fi + 1,
            "shards_total": len(files),
            "total_docs": total_docs,
            "total_tokens": total_tokens,
            "total_tokens_billions": total_tokens / 1e9,
            "total_chars": total_chars,
            "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
            "by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])),
            "by_license": dict(sorted(by_license.items(), key=lambda x: -x[1])),
            "by_country": dict(sorted(by_country.items(), key=lambda x: -x[1])),
            "by_language_variant": dict(sorted(by_lang_variant.items(), key=lambda x: -x[1])),
        }
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n=== FINAL ===", flush=True)
    print(f"Total docs:   {total_docs:,}", flush=True)
    print(f"Total tokens: {total_tokens:,}  ({total_tokens/1e9:.3f}B, {args.encoding})", flush=True)
    print(f"Written: {args.out}", flush=True)


if __name__ == "__main__":
    main()
