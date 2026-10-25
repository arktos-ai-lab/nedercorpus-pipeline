"""
embed_shard.py — Embed a shard of NederCorpus/GPT-NL jsonl docs through 5 models
for the teleological-constellation build.

Models (see MEMORY / MRL2026 discussion for rationale — each covers an axis the
others don't: general multilingual content, Dutch-specific content, sentiment,
legal-domain register, and cross-lingual/dialect-alignment). All 5 repos verified
to exist on the HF Hub via HfApi.model_info() on 2026-07-07:
  1. BAAI/bge-m3                                                          (1024d, general multilingual, 8192 ctx)
  2. NetherlandsForensicInstitute/robbert-2022-dutch-sentence-transformers (768d,  Dutch-specific)
  3. DTAI-KULeuven/robbert-v2-dutch-sentiment                             (768d,  Dutch sentiment/affect)
  4. joelniklaus/legal-xlm-roberta-large                                  (1024d, multilingual legal register)
  5. sentence-transformers/LaBSE                                         (768d,  cross-lingual/dialect alignment)

Resumable: counts total docs in the shard first, preallocates fp16 memmaps sized
to that count, and resumes from len(index.jsonl) on restart — safe to kill and
rerun at any point.

Usage:
  python embed_shard.py --shard_dir /root/shard0 --out_dir /root/emb_shard0 --batch_size 16
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MODEL_SPECS = [
    {"key": "bge_m3",  "repo": "BAAI/bge-m3",                                          "dim": 1024, "kind": "sbert", "max_len": 8192},
    {"key": "nl_sbert","repo": "NetherlandsForensicInstitute/robbert-2022-dutch-sentence-transformers", "dim": 768,  "kind": "sbert", "max_len": 512},
    {"key": "nl_sent", "repo": "DTAI-KULeuven/robbert-v2-dutch-sentiment",              "dim": 768,  "kind": "hf_mean_pool", "max_len": 512},
    {"key": "legal_xlmr","repo": "joelniklaus/legal-xlm-roberta-large",                 "dim": 1024, "kind": "hf_mean_pool", "max_len": 512},
    {"key": "labse",   "repo": "sentence-transformers/LaBSE",                          "dim": 768,  "kind": "sbert", "max_len": 256},
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--shard_dir", required=True, type=Path)
    p.add_argument("--out_dir",   required=True, type=Path)
    p.add_argument("--batch_size", default=16, type=int)
    p.add_argument("--max_docs",  default=None, type=int, help="For smoke-testing on a subset.")
    return p.parse_args()


def iter_shard_docs(shard_dir: Path, max_docs=None):
    """Yield (source_file, line_no, text) across all jsonl files in the shard, sorted for determinism."""
    n = 0
    for path in sorted(shard_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = rec.get("text", "")
                if not text:
                    continue
                yield path.name, line_no, text
                n += 1
                if max_docs and n >= max_docs:
                    return


def count_docs(shard_dir: Path, max_docs=None) -> int:
    n = 0
    for _ in iter_shard_docs(shard_dir, max_docs):
        n += 1
    return n


def load_models():
    import torch
    from sentence_transformers import SentenceTransformer
    from transformers import AutoModel, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    loaded = []
    for spec in MODEL_SPECS:
        print(f"Loading {spec['key']} ({spec['repo']}) …", flush=True)
        if spec["kind"] == "sbert":
            model = SentenceTransformer(spec["repo"], device=device, trust_remote_code=True)
            loaded.append({**spec, "model": model, "tokenizer": None})
        else:
            tok = AutoTokenizer.from_pretrained(spec["repo"])
            model = AutoModel.from_pretrained(spec["repo"]).to(device).eval()
            loaded.append({**spec, "model": model, "tokenizer": tok})
    return loaded, device


def mean_pool(last_hidden_state, attention_mask):
    import torch
    mask = attention_mask.unsqueeze(-1).expand(last_hidden_state.size()).float()
    summed = torch.sum(last_hidden_state * mask, dim=1)
    counts = torch.clamp(mask.sum(dim=1), min=1e-9)
    return summed / counts


def embed_batch(spec, texts, device):
    import torch
    if spec["kind"] == "sbert":
        vecs = spec["model"].encode(
            texts, batch_size=len(texts), show_progress_bar=False,
            normalize_embeddings=True, convert_to_numpy=True,
        )
        return vecs.astype(np.float16)
    else:
        tok = spec["tokenizer"]
        enc = tok(texts, padding=True, truncation=True, max_length=spec["max_len"], return_tensors="pt").to(device)
        with torch.no_grad():
            out = spec["model"](**enc)
        pooled = mean_pool(out.last_hidden_state, enc["attention_mask"])
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        return pooled.cpu().numpy().astype(np.float16)


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    index_path = args.out_dir / "index.jsonl"
    done_path = args.out_dir / "DONE"

    if done_path.exists():
        print("Already complete (DONE marker present). Exiting.")
        return

    print("Counting docs in shard …", flush=True)
    total_docs = count_docs(args.shard_dir, args.max_docs)
    print(f"Shard has {total_docs:,} docs.", flush=True)

    # Preallocate fp16 memmaps, one per model.
    memmaps = {}
    for spec in MODEL_SPECS:
        mm_path = args.out_dir / f"emb_{spec['key']}.f16"
        mode = "r+" if mm_path.exists() and mm_path.stat().st_size == total_docs * spec["dim"] * 2 else "w+"
        memmaps[spec["key"]] = np.memmap(mm_path, dtype=np.float16, mode=mode, shape=(total_docs, spec["dim"]))

    n_done = 0
    if index_path.exists():
        with open(index_path, encoding="utf-8") as fh:
            n_done = sum(1 for _ in fh)
        print(f"Resuming from doc {n_done:,}/{total_docs:,}", flush=True)

    print("Loading 5 models …", flush=True)
    models, device = load_models()

    index_fh = open(index_path, "a", encoding="utf-8")
    batch, batch_meta = [], []
    n_seen = 0
    t0 = time.time()

    def flush_batch():
        nonlocal batch, batch_meta
        if not batch:
            return
        start = batch_meta[0][0]
        for spec in models:
            vecs = embed_batch(spec, batch, device)
            memmaps[spec["key"]][start:start + len(batch)] = vecs
        for idx, (doc_idx, source_file, line_no, n_chars) in enumerate(batch_meta):
            index_fh.write(json.dumps({"doc_idx": doc_idx, "source_file": source_file,
                                        "line_no": line_no, "n_chars": n_chars}) + "\n")
        index_fh.flush()
        batch, batch_meta = [], []

    for source_file, line_no, text in iter_shard_docs(args.shard_dir, args.max_docs):
        if n_seen < n_done:
            n_seen += 1
            continue
        batch.append(text)
        batch_meta.append((n_seen, source_file, line_no, len(text)))
        n_seen += 1
        if len(batch) >= args.batch_size:
            flush_batch()
            if n_seen % (args.batch_size * 50) == 0:
                rate = (n_seen - n_done) / max(time.time() - t0, 1e-6)
                eta_h = (total_docs - n_seen) / max(rate, 1e-6) / 3600
                print(f"  {n_seen:,}/{total_docs:,} docs | {rate:.1f} docs/sec | ETA {eta_h:.1f}h", flush=True)
    flush_batch()

    for mm in memmaps.values():
        mm.flush()
    index_fh.close()
    done_path.write_text("ok", encoding="utf-8")
    print(f"\nDone. {total_docs:,} docs embedded across {len(MODEL_SPECS)} models -> {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
