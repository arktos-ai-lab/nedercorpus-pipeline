#!/usr/bin/env bash
# run_remote.sh — Run this on the vast.ai instance after data is uploaded.
# Trains both models sequentially then evaluates. Total ~12h on RTX 3090.

set -e
cd /workspace

echo "=== Training NL-only model (~6h) ==="
python scripts/variety_experiment/train_lm.py \
    --split nl_only \
    --out_dir experiment_data \
    2>&1 | tee logs_nl_only.txt

echo "=== Training full-variety model (~6h) ==="
python scripts/variety_experiment/train_lm.py \
    --split full \
    --out_dir experiment_data \
    2>&1 | tee logs_full.txt

echo "=== Evaluating perplexity by variety ==="
python scripts/variety_experiment/eval_ppl.py \
    --out_dir experiment_data \
    2>&1 | tee logs_eval.txt

echo "=== Done. Fetch results: ==="
echo "  scp -P PORT root@HOST:/workspace/experiment_data/results.md ."
echo "  scp -P PORT root@HOST:/workspace/experiment_data/results.json ."
