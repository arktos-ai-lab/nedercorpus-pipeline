#!/usr/bin/env bash
# EuroEval tier rerun (2026-10-10), author decision: 3 NL licence-tier conditions,
# shuffled splits from the relabelled build, identical 1.1B GPT-2-token budget, same
# model / hyperparameters / EuroEval command as the 2026-08 tier runs.
#
# Resumable at every level (repo CLAUDE.md):
#   stage 0  build_nl_tier_splits_v2.py  per-file index + per-split atomic writes, skip-if-done
#   stage 1  train_tier_resumable.py     cached tokens, checkpoint every 500 steps, resumes
#                                        from newest checkpoint-*, skips if final/ exists
#   stage 2  run_vast_eval_v2.py         per-condition skip if its result log is complete;
#                                        runs in the background while the next model trains
# Whole queue retried up to MAX_ATTEMPTS times. LAST step relaunches the Lazarus
# size-matched queue so the GPU is not left idle.
#
# Launch detached (Git Bash):
#   powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'C:\Program Files\Git\bin\bash.exe' -ArgumentList '-c','bash /e/GitHub/Arktos-AI-lab-project-library/90-published-and-review/90-40-to-review/20-21-nedercorpus/scripts/euroeval_experiment/run_euroeval_v2_queue.sh >> /f/NederCorpus/scripts/euroeval_experiment/euroeval_v2_queue.log 2>&1'"
set -u
CODE=/e/GitHub/Arktos-AI-lab-project-library/90-published-and-review/90-40-to-review/20-21-nedercorpus/scripts/euroeval_experiment
WORK=/f/NederCorpus/scripts/euroeval_experiment
RESULTS=/e/GitHub/Arktos-AI-lab-project-library/90-published-and-review/90-40-to-review/20-21-nedercorpus/experiment_results/euroeval_v2
CONDS="tier1_nl tier2_nl tier3_nl"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-4}"
EVAL_ATTEMPTS="${EVAL_ATTEMPTS:-2}"
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
cd "$WORK" || exit 1
mkdir -p logs_v2 "$RESULTS"

eval_done() { [ -f "$RESULTS/v2_$1.log" ] && grep -q "Completed 9 benchmarks" "$RESULTS/v2_$1.log"; }

eval_cond() {  # runs in background; bounded retries (each attempt rents one vast.ai box)
  local c="$1" a=1
  mkdir "logs_v2/eval_$c.lock" 2>/dev/null || { echo "[eval] v2_$c already running"; return 0; }
  while ! eval_done "$c" && [ "$a" -le "$EVAL_ATTEMPTS" ]; do
    echo "[eval] v2_$c attempt $a [$(date '+%F %T')]"
    python -P "$CODE/run_vast_eval_v2.py" "v2_$c" >> "logs_v2/eval_v2_$c.log" 2>&1
    a=$((a + 1))
  done
  if eval_done "$c"; then echo "[eval] v2_$c DONE [$(date '+%F %T')]"; else echo "[eval] v2_$c FAILED after $EVAL_ATTEMPTS attempts [$(date '+%F %T')]"; fi
  rmdir "logs_v2/eval_$c.lock" 2>/dev/null
}

run_all() {
  echo "=== stage 0: tier splits [$(date '+%F %T')] ==="
  python -P "$CODE/build_nl_tier_splits_v2.py" >> build_tier_splits_v2.log 2>&1 || return 1
  for c in $CONDS; do
    [ -f "tier_splits_v2/$c.meta.json" ] || { echo "missing split $c"; return 1; }
  done
  for c in $CONDS; do
    if [ ! -f "checkpoints/checkpoint_v2_${c}_seed42/final/train_meta.json" ]; then
      echo "=== stage 1: train v2_$c [$(date '+%F %T')] ==="
      python -P "$CODE/train_tier_resumable.py" --input "tier_splits_v2/$c.jsonl" \
        --label "v2_$c" --out_dir checkpoints >> "logs_v2/train_v2_$c.log" 2>&1 || return 1
      echo "=== trained v2_$c [$(date '+%F %T')] ==="
    else
      echo "[skip] v2_$c already trained"
    fi
    if ! eval_done "$c"; then eval_cond "$c" & fi
  done
  return 0
}

echo "=== EuroEval v2 queue start pid=$$ [$(date '+%F %T')] ==="
rmdir logs_v2/eval_*.lock 2>/dev/null   # stale locks from a previous (dead) queue process
attempt=1
until run_all; do
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "!!! training gave up after $attempt attempts [$(date '+%F %T')]; re-run to resume"
    break
  fi
  attempt=$((attempt + 1))
  echo "--- stage failed; retry $attempt/$MAX_ATTEMPTS in 60s (resumes from checkpoints) ---"
  sleep 60
done
wait   # background evals
for c in $CONDS; do eval_done "$c" || eval_cond "$c"; done
echo "=== EuroEval v2 queue finished [$(date '+%F %T')] ==="
for c in $CONDS; do eval_done "$c" && echo "  v2_$c: eval complete" || echo "  v2_$c: eval MISSING"; done

echo "=== relaunching Lazarus size-matched queue [$(date '+%F %T')] ==="
cd /e/GitHub/Arktos-AI-lab-project-library/40-esolangs-lazarus/01-flagship-esolang-corpus && \
  bash run_overnight_queue_sizematched.sh >> results/overnight_sizematched.log 2>&1
