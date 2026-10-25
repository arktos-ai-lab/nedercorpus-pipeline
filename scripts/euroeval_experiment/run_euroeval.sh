#!/usr/bin/env bash
# run_euroeval.sh — Evaluate a continued-pretrained model on Dutch EuroEval tasks.
#
# Usage:
#   bash run_euroeval.sh <model_dir> <output_label>
#
# Requires: pip install euroeval
# EuroEval Dutch tasks: mmlu-nl, squad-nl, scala-nl, wikilingua-nl, belebele-nl

set -e
MODEL_DIR=$1
LABEL=$2
OUT_DIR="euroeval_results/${LABEL}"
mkdir -p "$OUT_DIR"

TASKS="mmlu-nl squad-nl scala-nl wikilingua-nl belebele-nl"

for TASK in $TASKS; do
    echo "=== $TASK ==="
    euroeval \
        --model "$MODEL_DIR" \
        --dataset "$TASK" \
        --output_dir "$OUT_DIR" \
        --batch_size 8 \
        --no_progress_bar \
        2>&1 | tee "$OUT_DIR/${TASK}.log"
done

echo "=== Results ==="
cat "$OUT_DIR"/*.log | grep -E "^(Task|Score|F1|EM|ROUGE|Accuracy)" | tee "$OUT_DIR/summary.txt"
