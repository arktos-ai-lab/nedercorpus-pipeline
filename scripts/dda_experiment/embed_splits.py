"""
embed_splits.py — DDA Experiment Phase 1: embed corpus splits with 11 multilingual models.

For each model × corpus combination:
  - Reservoir-sample SAMPLE_SIZE documents from the JSONL split
  - Batch-encode with SentenceTransformers
  - Save: embeddings/<label>/<short_name>.npy   float32 [N, D]
          embeddings/<label>/meta.json           {model_id, n_docs, dim, label}

Corpus labels discovered automatically from tier_splits/*.jsonl
(tier1_nl, tier1_all, tier2_nl, tier2_all, tier3_nl, tier3_all)
plus any explicitly passed --extra_corpus files (gptnl_8b, gptnl_36b).

Usage:
  # All 6 NederCorpus splits, all 11 models
  python embed_splits.py

  # Add GPT-NL corpora
  python embed_splits.py --extra tier_splits/gptnl_8b.jsonl tier_splits/gptnl_36b.jsonl

  # Specific models only
  python embed_splits.py --models mMiniLM LaBSE BGE-M3 mE5-base

  # Dry-run: show what would run, skip existing
  python embed_splits.py --dry_run

Requirements:
  pip install sentence-transformers torch tqdm numpy
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Iterator

import numpy as np

SPLITS_DIR  = Path(__file__).resolve().parent / "tier_splits"
EMBED_DIR   = Path(__file__).resolve().parent / "embeddings"
SAMPLE_SIZE = 100_000
SEED        = 42

# 11 models: short_name → (hf_id, passage_prefix_or_None)
# mE5 family requires "passage: " prefix for corpus documents.
# Other models work without prefix.
MODELS: dict[str, tuple[str, str | None]] = {
    "mMiniLM":   ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", None),
    "distilUSE": ("sentence-transformers/distiluse-base-multilingual-cased-v2",  None),
    "mMPNet":    ("sentence-transformers/paraphrase-multilingual-mpnet-base-v2", None),
    "LaBSE":     ("sentence-transformers/LaBSE",                                 None),
    "mE5-sm":    ("intfloat/multilingual-e5-small",                              "passage: "),
    "mE5-base":  ("intfloat/multilingual-e5-base",                               "passage: "),
    "mE5-lg":    ("intfloat/multilingual-e5-large",                              "passage: "),
    "BGE-M3":    ("BAAI/bge-m3",                                                 None),
    "GTE-multi": ("Alibaba-NLP/gte-multilingual-base",                           None),
    "NL-allNLI": ("textgain/allnli-GroNLP-bert-base-dutch-cased",               None),
    "XLM-R-STS": ("sentence-transformers/stsb-xlm-r-multilingual",              None),
}

BATCH_SIZES: dict[str, int] = {
    "mMiniLM":   512,
    "distilUSE": 256,
    "mMPNet":    256,
    "LaBSE":     256,
    "mE5-sm":    512,
    "mE5-base":  256,
    "mE5-lg":    24,
    "BGE-M3":    16,
    "GTE-multi": 24,
    "NL-allNLI": 256,
    "XLM-R-STS": 256,
}

# Max text length to pass to encoder (chars); avoids OOM on very long docs
MAX_CHARS = 2000


def reservoir_sample_texts(path: Path, n: int, seed: int) -> list[str]:
    """Single-pass reservoir sample of n texts from a JSONL file."""
    rng    = random.Random(seed)
    sample = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                text = json.loads(line).get("text", "")
            except json.JSONDecodeError:
                continue
            if not text or len(text.split()) < 10:
                continue
            text = text[:MAX_CHARS]
            if len(sample) < n:
                sample.append(text)
            else:
                j = rng.randint(0, i)
                if j < n:
                    sample[j] = text
    rng.shuffle(sample)
    return sample


def embed(
    texts: list[str],
    model_key: str,
    prefix: str | None,
    batch_size: int,
) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"    Loading {model_key} on {device} ...", flush=True)

    try:
        model = SentenceTransformer(MODELS[model_key][0], device=device, trust_remote_code=True)
    except Exception as e:
        print(f"    WARNING: could not load {model_key}: {e}")
        return None

    # GTE-multi (Alibaba-NLP/gte-multilingual-base): transformers >=5's fast-init path
    # zeroes out the model's non-persistent `position_ids` buffer instead of running the
    # torch.arange(...) in NewEmbeddings.__init__, causing an out-of-bounds gather in RoPE
    # indexing (rope_cos[position_ids]) on first inference. Rebuild it manually post-load.
    auto_model = getattr(model[0], "auto_model", None) if len(model) else None
    if auto_model is not None and hasattr(auto_model, "embeddings") and hasattr(auto_model.embeddings, "position_ids"):
        auto_model.embeddings.position_ids = torch.arange(
            auto_model.config.max_position_embeddings, device=device
        )

    if prefix:
        texts = [prefix + t for t in texts]

    print(f"    Encoding {len(texts):,} texts (batch={batch_size}) ...", flush=True)
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,    # unit sphere — cosine = dot product
    )
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return vecs.astype(np.float32)


def run_corpus(corpus_path: Path, label: str, model_keys: list[str], dry_run: bool) -> None:
    out_dir = EMBED_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Corpus: {label}  ({corpus_path.name})")

    # Sample texts once — same sample for all models
    sample_cache = out_dir / "_texts_sample.json"
    if sample_cache.exists():
        print(f"  Loading cached sample ({SAMPLE_SIZE:,} texts) ...", flush=True)
        with open(sample_cache, encoding="utf-8") as f:
            texts = json.load(f)
    else:
        print(f"  Reservoir sampling {SAMPLE_SIZE:,} texts ...", flush=True)
        if dry_run:
            print("  [dry-run] skipping sample")
            texts = []
        else:
            texts = reservoir_sample_texts(corpus_path, SAMPLE_SIZE, SEED)
            print(f"  Sampled {len(texts):,} texts", flush=True)
            with open(sample_cache, "w", encoding="utf-8") as f:
                json.dump(texts, f, ensure_ascii=False)

    for model_key in model_keys:
        out_npy  = out_dir / f"{model_key}.npy"
        out_meta = out_dir / f"{model_key}.json"
        if out_npy.exists():
            print(f"  [{model_key}] already exists — skipping")
            continue
        if dry_run:
            print(f"  [{model_key}] would embed {len(texts):,} texts")
            continue

        print(f"\n  [{model_key}] {MODELS[model_key][0]}")
        prefix     = MODELS[model_key][1]
        batch_size = BATCH_SIZES.get(model_key, 256)

        try:
            vecs = embed(texts, model_key, prefix, batch_size)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        if vecs is None:
            continue

        np.save(out_npy, vecs)
        with open(out_meta, "w", encoding="utf-8") as f:
            json.dump({
                "model_key": model_key,
                "model_id": MODELS[model_key][0],
                "label": label,
                "n_docs": int(vecs.shape[0]),
                "dim": int(vecs.shape[1]),
                "sample_size": SAMPLE_SIZE,
                "seed": SEED,
                "normalized": True,
            }, f, indent=2)

        mb = out_npy.stat().st_size / 1e6
        print(f"    Saved {vecs.shape} -> {out_npy.name}  ({mb:.0f} MB)", flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--splits_dir", type=Path, default=SPLITS_DIR)
    p.add_argument("--extra",      type=Path, nargs="*", default=[],
                   help="Additional JSONL files to embed (e.g. gptnl_8b.jsonl)")
    p.add_argument("--models",     nargs="*", default=list(MODELS.keys()),
                   choices=list(MODELS.keys()),
                   help="Which models to run (default: all 11)")
    p.add_argument("--out_dir",    type=Path, default=EMBED_DIR)
    p.add_argument("--sample_size", type=int, default=SAMPLE_SIZE)
    p.add_argument("--dry_run",    action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    global EMBED_DIR, SAMPLE_SIZE
    EMBED_DIR   = args.out_dir
    SAMPLE_SIZE = args.sample_size

    # Auto-discover NederCorpus tier splits
    tier_files: list[tuple[Path, str]] = []
    if args.splits_dir.is_dir():
        for f in sorted(args.splits_dir.glob("tier*.jsonl")):
            tier_files.append((f, f.stem))
    else:
        print(f"WARNING: splits_dir {args.splits_dir} not found")

    # Extra corpora (GPT-NL etc.)
    extra_files: list[tuple[Path, str]] = []
    for f in (args.extra or []):
        if f.exists():
            extra_files.append((f, f.stem))
        else:
            print(f"WARNING: {f} not found — skipping")

    all_corpora = tier_files + extra_files

    if not all_corpora:
        sys.exit("ERROR: no corpora found. Pass --splits_dir or --extra.")

    print(f"DDA Embedding Experiment")
    print(f"  Corpora ({len(all_corpora)}): {[l for _, l in all_corpora]}")
    print(f"  Models  ({len(args.models)}): {args.models}")
    print(f"  Sample:  {args.sample_size:,} docs per corpus")
    print(f"  Output:  {args.out_dir}")
    if args.dry_run:
        print("  [DRY RUN — no embeddings will be computed]")

    for corpus_path, label in all_corpora:
        run_corpus(corpus_path, label, args.models, args.dry_run)

    print("\nAll done.")
    print(f"Embeddings in: {args.out_dir}/")
    print("Next: python analyze_dda.py")


if __name__ == "__main__":
    main()
