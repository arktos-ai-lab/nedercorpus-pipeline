#!/usr/bin/env bash
# run_all_tiers_local.sh — Local version of run_all_tiers.sh for the RTX 5090
# laptop GPU. Differences from the vast.ai version:
#   - Training runs under the default (Windows) Python 3.14 env — that's
#     where torch/transformers/accelerate already live.
#   - Evaluation runs EuroEval INSIDE WSL2 (Ubuntu), not on native Windows
#     at all: EuroEval's generative-model support hard-requires `vllm`,
#     which upstream restricts to Linux in its own dependency spec (see
#     euroeval's METADATA: vllm/bitsandbytes/ray/fbgemm-gpu are all gated
#     `platform_system == 'Linux'`). It cannot be made to work on native
#     Windows Python no matter what extras are installed — confirmed after
#     `pip install euroeval[generative]` on Windows still left `vllm`
#     entirely missing. WSL2 has full GPU passthrough to the same 5090
#     (verified via `nvidia-smi` inside WSL), so this stays 100% local.
#   - gptnl_1b / gptnl_8b stream directly from the existing dda_experiment
#     reference corpora (gptnl_14b.jsonl / gptnl_all_nl_fy.jsonl) instead of
#     a dedicated split file — train_tier.py caps at --max_tokens regardless
#     of input file size, so no separate prep step is needed.
#   - Uses `euroeval -m <ckpt> -l nl --evaluate-test-split` (current 16.x
#     CLI) instead of the older --dataset/--output_dir/--batch_size flags
#     the original run_euroeval.sh assumed.
#
# Usage: bash scripts/euroeval_experiment/run_all_tiers_local.sh

set -e
set -o pipefail  # without this, `cmd | tee log` reports tee's exit code, not
                  # cmd's — a training or eval crash was silently swallowed and
                  # the loop moved on to the next condition as if nothing failed.
cd "$(dirname "$0")"
SEED=42  # train_tier.py's own default; must match since we never pass --seed
# Training uses the default (system) Python 3.14 env — that's where
# torch/transformers/accelerate already live (it trained the seed-42
# baseline). Only euroeval + its scikit-learn dependency needed the
# separate Python 3.11 venv, since no scikit-learn wheel exists for
# cp314 on Windows and building it from source fails (MSVC sanity-check
# error in this environment).
TRAIN_PY="python"
WSL_REPO_DIR="/mnt/f/NederCorpus/scripts/euroeval_experiment"

mkdir -p checkpoints euroeval_results

declare -A INPUT_FOR
INPUT_FOR[tier1_nl]=tier_splits/tier1_nl.jsonl
INPUT_FOR[tier1_all]=tier_splits/tier1_all.jsonl
INPUT_FOR[tier2_nl]=tier_splits/tier2_nl.jsonl
INPUT_FOR[tier2_all]=tier_splits/tier2_all.jsonl
INPUT_FOR[tier3_nl]=tier_splits/tier3_nl.jsonl
INPUT_FOR[tier3_all]=tier_splits/tier3_all.jsonl
INPUT_FOR[gptnl_1b]=../dda_experiment/tier_splits/gptnl_14b.jsonl
INPUT_FOR[gptnl_8b]=../dda_experiment/tier_splits/gptnl_all_nl_fy.jsonl

declare -A MAXTOK_FOR
MAXTOK_FOR[tier1_nl]=1100000000
MAXTOK_FOR[tier1_all]=1100000000
MAXTOK_FOR[tier2_nl]=1100000000
MAXTOK_FOR[tier2_all]=1100000000
MAXTOK_FOR[tier3_nl]=1100000000
MAXTOK_FOR[tier3_all]=1100000000
MAXTOK_FOR[gptnl_1b]=1100000000
MAXTOK_FOR[gptnl_8b]=8500000000

CONDITIONS=(tier1_nl tier1_all tier2_nl tier2_all tier3_nl tier3_all gptnl_1b gptnl_8b)

for COND in "${CONDITIONS[@]}"; do
  SPLIT_FILE="${INPUT_FOR[$COND]}"
  if [[ ! -f "$SPLIT_FILE" ]]; then
    echo "SKIP $COND: $SPLIT_FILE not found"
    continue
  fi
  CKPT="checkpoints/checkpoint_${COND}_seed${SEED}/final"
  if [[ -d "$CKPT" ]]; then
    echo "=== $COND: checkpoint already exists, skipping training ==="
  else
    echo "=== Training $COND (max_tokens=${MAXTOK_FOR[$COND]}) ==="
    "$TRAIN_PY" train_tier.py \
        --input "$SPLIT_FILE" \
        --label "$COND" \
        --out_dir checkpoints \
        --max_tokens "${MAXTOK_FOR[$COND]}" \
        2>&1 | tee "logs_train_${COND}.txt"
  fi

  echo "=== Evaluating $COND on EuroEval (Dutch, via WSL2) ==="
  mkdir -p "euroeval_results/${COND}"
  # HF_TOKEN= (empty, not unset) blocks python-dotenv from auto-loading the
  # stale/invalid HF_TOKEN in F:/NederCorpus/.env — dotenv's default
  # override=False skips keys already present in the environment, even
  # empty ones, but `env -u` (fully absent) does NOT stop it from being
  # reloaded from .env. This bit the smoke test three times before being
  # found; see .cache/huggingface/token.invalid.bak for the other stale
  # credential that had to be moved aside for the same reason (WSL has its
  # own separate HF cache, so that specific fix doesn't need to repeat there).
  # nvcc is installed in WSL (/usr/local/cuda/bin) but not on the default
  # PATH — vllm JIT-compiles some kernels and needs it at runtime, not just
  # the CUDA driver.
  # Non-fatal (|| true): WSL2/vLLM eval is still being debugged (hit a real
  # deadlock earlier under VLLM_WSL2_ENABLE_PIN_MEMORY=1, omitted here). A
  # failed eval must not stop training on the remaining conditions — that's
  # the valuable, working part of this run. Re-run eval separately per
  # checkpoint once the WSL2/vLLM issue is resolved.
  wsl -d Ubuntu -- bash -lc \
      "cd '$WSL_REPO_DIR' && PATH=\"/usr/local/cuda/bin:\$PATH\" HF_TOKEN= /mnt/f/NederCorpus/.venv_euroeval_wsl/bin/euroeval -m '$CKPT' -l nl --evaluate-test-split --no-progress-bar --generative-type base" \
      2>&1 | tee "euroeval_results/${COND}/log.txt" || echo "  (eval failed for $COND — continuing to next condition)"
done

echo "=== Done. Per-condition logs in euroeval_results/*/log.txt ==="
