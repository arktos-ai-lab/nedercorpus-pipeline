#!/usr/bin/env bash
# run_remote_multiseed.sh — Run this on the GPU instance after data is uploaded.
# Trains 3 conditions (nl_only, full, gptnl_af_fy) x 3 seeds (9 runs total),
# then evaluates all of them together.
#
# Cost note: this is ~4.5x the original single-seed, 2-condition run. At
# ~6h/run on an RTX 3090 for nl_only/full, and somewhat more for gptnl_af_fy
# (token budget depends on how much GPT-NL (nl+fy, incl. youtube-commons) +
# NederCorpus (NL/FY/AF/LI/ZEA/PAP, each capped at 500M tok — see
# prepare_gptnl_af_fy_split.py) data
# survives the holdout-leakage filter — check the prepare step's printed
# token count before assuming ~6h there too). Budget ~55-65h total before
# renting. Requires: pip install datasets (for the GPT-NL HF pull).

set -e
cd /workspace

SEEDS=(42 43 44)

echo "=== Preparing gptnl_af_fy split (one-time, not per-seed) ==="
python scripts/variety_experiment/prepare_gptnl_af_fy_split.py \
    --normalized_dir data/normalized \
    --holdout experiment_data/holdout.jsonl \
    --out experiment_data/train_gptnl_af_fy.jsonl \
    --gptnl_target_tokens 1400000000 \
    2>&1 | tee logs_prepare_gptnl_af_fy.txt

for SEED in "${SEEDS[@]}"; do
  echo "=== Training nl_only, seed $SEED (~6h) ==="
  python scripts/variety_experiment/train_lm.py \
      --split nl_only \
      --seed "$SEED" \
      --out_dir experiment_data \
      2>&1 | tee "logs_nl_only_seed${SEED}.txt"

  echo "=== Training full, seed $SEED (~6h) ==="
  python scripts/variety_experiment/train_lm.py \
      --split full \
      --seed "$SEED" \
      --out_dir experiment_data \
      2>&1 | tee "logs_full_seed${SEED}.txt"

  echo "=== Training gptnl_af_fy, seed $SEED ==="
  python scripts/euroeval_experiment/train_tier.py \
      --input experiment_data/train_gptnl_af_fy.jsonl \
      --label gptnl_af_fy \
      --seed "$SEED" \
      --out_dir experiment_data \
      --max_tokens 2000000000 \
      2>&1 | tee "logs_gptnl_af_fy_seed${SEED}.txt"
done

echo "=== Evaluating perplexity across all seeds and conditions ==="
python scripts/variety_experiment/eval_ppl_multiseed.py \
    --out_dir experiment_data \
    --seeds "${SEEDS[@]}" \
    --splits nl_only full gptnl_af_fy \
    2>&1 | tee logs_eval_multiseed.txt

echo "=== Done. Fetch results: ==="
echo "  scp -P PORT root@HOST:/workspace/experiment_data/results_multiseed.md ."
echo "  scp -P PORT root@HOST:/workspace/experiment_data/results_multiseed.json ."
