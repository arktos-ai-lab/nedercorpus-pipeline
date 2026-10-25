#!/usr/bin/env bash
# run_all_tiers.sh — Train + evaluate every condition in the license-tier /
# data-abundance ablation, then aggregate results into one table.
#
# Conditions (7, not 9 — gptnl_36b is byte-identical to gptnl_8b because
# NederCorpus's full corpus, 8.12B tokens, is smaller than 36B: there is
# nothing to distinguish those two runs, so we skip the duplicate and note
# it as a finding in results.md instead of silently training it twice):
#   tier1_nl   tier1_all   tier2_nl   tier2_all   tier3_nl   tier3_all   (~1B tok each)
#   gptnl_1b                                                             (~1B tok, all-license)
#   gptnl_8b                                                             (~8.12B tok = FULL corpus)
#
# COST WARNING: the first 7 runs are ~1B-token budgets, comparable to the
# variety_experiment runs (~6h each on an RTX 3090-class GPU). gptnl_8b is a
# full-corpus run at ~8x the token budget — expect roughly 8x the wall clock
# of a single tier run for that condition alone. Total estimated wall clock
# on a single RTX 3090: 6 x ~6h + ~6h (gptnl_1b) + ~48h (gptnl_8b) ~= 96h.
# This is a real money decision on a rented GPU — check vast.ai pricing
# before launching and consider running gptnl_8b on a faster card separately.
#
# Usage (run on the GPU instance after tier_splits/ is uploaded):
#   bash run_all_tiers.sh
#   bash run_all_tiers.sh --skip-gptnl8b   # cheaper: skip the full-corpus run

set -e
cd "$(dirname "$0")"

SKIP_GPTNL8B=false
[[ "$1" == "--skip-gptnl8b" ]] && SKIP_GPTNL8B=true

CONDITIONS=(tier1_nl tier1_all tier2_nl tier2_all tier3_nl tier3_all gptnl_1b)
$SKIP_GPTNL8B || CONDITIONS+=(gptnl_8b)

mkdir -p checkpoints euroeval_results

for COND in "${CONDITIONS[@]}"; do
  SPLIT_FILE="tier_splits/${COND}.jsonl"
  if [[ ! -f "$SPLIT_FILE" ]]; then
    echo "SKIP $COND: $SPLIT_FILE not found"
    continue
  fi
  CKPT="checkpoints/checkpoint_${COND}_seed42/final"
  if [[ -d "$CKPT" ]]; then
    echo "=== $COND: checkpoint already exists, skipping training ==="
  else
    echo "=== Training $COND ==="
    MAX_TOK=1100000000
    [[ "$COND" == "gptnl_8b" ]] && MAX_TOK=8500000000
    python train_tier.py \
        --input "$SPLIT_FILE" \
        --label "$COND" \
        --out_dir checkpoints \
        --max_tokens "$MAX_TOK" \
        2>&1 | tee "logs_train_${COND}.txt"
  fi

  echo "=== Evaluating $COND on EuroEval ==="
  bash run_euroeval.sh "$CKPT" "$COND" 2>&1 | tee "logs_euroeval_${COND}.txt"
done

echo "=== Aggregating results ==="
python aggregate_results.py --results_dir euroeval_results --out results_tiers.md

echo "=== Done. See results_tiers.md ==="
