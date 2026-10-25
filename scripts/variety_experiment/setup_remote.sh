#!/usr/bin/env bash
# setup_remote.sh — Run this ONCE on the vast.ai instance after SSH-ing in.
# It installs dependencies and sets up the directory structure.

set -e

pip install -q transformers datasets accelerate torch --upgrade

mkdir -p /workspace/experiment_data
mkdir -p /workspace/scripts/variety_experiment

echo "Setup complete. Now upload your data files and run the experiment."
echo ""
echo "Upload commands (run from YOUR local machine):"
echo "  scp -P PORT experiment_data/holdout.jsonl       root@HOST:/workspace/experiment_data/"
echo "  scp -P PORT experiment_data/train_nl_only.jsonl root@HOST:/workspace/experiment_data/"
echo "  scp -P PORT experiment_data/train_full.jsonl    root@HOST:/workspace/experiment_data/"
echo "  scp -P PORT scripts/variety_experiment/train_lm.py root@HOST:/workspace/scripts/variety_experiment/"
echo "  scp -P PORT scripts/variety_experiment/eval_ppl.py root@HOST:/workspace/scripts/variety_experiment/"
