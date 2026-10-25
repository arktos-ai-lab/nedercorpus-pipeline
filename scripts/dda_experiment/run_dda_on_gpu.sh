#!/usr/bin/env bash
# run_dda_on_gpu.sh — Full DDA experiment pipeline on a GPU instance.
#
# Assumes this repo is in /workspace/NederCorpus
# and tier splits are in scripts/euroeval_experiment/tier_splits/
#
# Approx wall time on A100 (100K docs × 11 models × 8 corpora):
#   Embedding:      ~3-5h   (sequential model loading, all corpora per model)
#   GPT-NL sample: ~30 min  (streaming from HuggingFace)
#   Analysis:       ~20 min (CPU, no GPU needed)
#   Total:          ~4-6h

set -euo pipefail
cd /workspace/NederCorpus

pip install -q sentence-transformers datasets tqdm numpy scipy scikit-learn

SPLITS_DIR="scripts/euroeval_experiment/tier_splits"
DDA_DIR="scripts/dda_experiment"
GPTNL_DATASET="${GPTNL_DATASET:-yhavinga/gptnl-public}"

# Step 1: Download GPT-NL 8B + 36B samples in one streaming pass
echo "=== Step 1: Sample GPT-NL ==="
if [ ! -f "${SPLITS_DIR}/gptnl_8b.jsonl" ] || [ ! -f "${SPLITS_DIR}/gptnl_36b.jsonl" ]; then
    python "${DDA_DIR}/sample_gptnl.py" \
        --dataset "${GPTNL_DATASET}" \
        --out_dir "${SPLITS_DIR}"
else
    echo "gptnl_8b.jsonl and gptnl_36b.jsonl already exist -- skipping"
fi

# Step 2: Embed all splits (NederCorpus + GPT-NL)
echo ""
echo "=== Step 2: Embed all corpora ==="
python "${DDA_DIR}/embed_splits.py" \
    --splits_dir "${SPLITS_DIR}" \
    --extra "${SPLITS_DIR}/gptnl_8b.jsonl" "${SPLITS_DIR}/gptnl_36b.jsonl" \
    2>&1 | tee "${DDA_DIR}/logs_embed.txt"

# Step 3: Analyse
echo ""
echo "=== Step 3: DDA Analysis ==="
python "${DDA_DIR}/analyze_dda.py" \
    --embed_dir "${DDA_DIR}/embeddings" \
    --reference gptnl_8b gptnl_36b \
    2>&1 | tee "${DDA_DIR}/logs_analyze.txt"

echo ""
echo "=== Done. Results in ${DDA_DIR}/dda_results/ ==="
cat "${DDA_DIR}/dda_results/report.md"
