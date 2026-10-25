#!/usr/bin/env bash
# run_experiment.sh — Full pipeline for the multi-variety training experiment.
#
# Run this on the rented GPU machine after:
#   git clone / rsync the repo
#   pip install transformers datasets accelerate torch
#
# Usage: run_experiment.sh [target_tokens] [out_dir]
#   e.g. run_experiment.sh 2000000000 experiment_data_2b

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_TOKENS="${1:-1000000000}"
OUT_DIR="$REPO_ROOT/${2:-experiment_data}"

cd "$REPO_ROOT"

echo "=== Step 1: prepare splits (target_tokens=$TARGET_TOKENS) ==="
python "$SCRIPT_DIR/prepare_splits.py" \
    --normalized_dir data/normalized \
    --out_dir "$OUT_DIR" \
    --target_tokens "$TARGET_TOKENS"

echo "=== Step 2a: train NL-only model ==="
python "$SCRIPT_DIR/train_lm.py" \
    --split nl_only \
    --out_dir "$OUT_DIR" \
    --target_tokens "$TARGET_TOKENS"

echo "=== Step 2b: train full-variety model ==="
python "$SCRIPT_DIR/train_lm.py" \
    --split full \
    --out_dir "$OUT_DIR" \
    --target_tokens "$TARGET_TOKENS"

echo "=== Step 3: evaluate perplexity by variety ==="
python "$SCRIPT_DIR/eval_ppl.py" \
    --out_dir "$OUT_DIR"

echo "=== Done. Results in $OUT_DIR/results.md ==="
