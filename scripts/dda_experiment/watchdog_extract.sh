#!/usr/bin/env bash
# watchdog_extract.sh — auto-restart extract_gptnl_full.py whenever it stalls.
#
# This machine's network path to huggingface.co's CDN has intermittent
# multi-minute DNS/TLS hangs that outlive curl's own --max-time in some cases
# (observed: a single shard fetch attempt stuck 30+ min with no forward
# progress and no error printed). Rather than debugging the exact root cause
# further, this watchdog just kills and resumes whenever progress.json's
# mtime goes stale for too long -- cheap, and shard-level checkpointing
# means a kill never loses more than the one in-flight shard.
set -u
cd "$(dirname "$0")"

PROGRESS=tier_splits/extract_gptnl_full_progress.json
SUMMARY=tier_splits/extract_gptnl_full_summary.json
STALE_SECS=360   # kill+restart if no checkpoint write in this long
CHECK_EVERY=30

log() { echo "[watchdog $(date '+%H:%M:%S')] $*"; }

start_extractor() {
    log "starting extractor (resume)"
    nohup /c/Python314/python extract_gptnl_full.py --resume >> extract_full.log 2>&1 &
    echo $!
}

pid=$(start_extractor)

while true; do
    sleep "$CHECK_EVERY"

    if [ -f "$SUMMARY" ]; then
        log "summary.json present -- extraction complete, exiting watchdog"
        exit 0
    fi

    # is the launched bash-side pid's underlying python still alive?
    if ! kill -0 "$pid" 2>/dev/null; then
        log "extractor process exited unexpectedly -- restarting"
        pid=$(start_extractor)
        continue
    fi

    if [ ! -f "$PROGRESS" ]; then
        continue  # hasn't written first checkpoint yet, give it time
    fi

    now=$(date +%s)
    mtime=$(stat -c %Y "$PROGRESS" 2>/dev/null || echo "$now")
    age=$((now - mtime))

    if [ "$age" -gt "$STALE_SECS" ]; then
        log "progress.json stale for ${age}s (> ${STALE_SECS}s) -- killing and restarting"
        kill -9 "$pid" 2>/dev/null
        # also nuke any orphaned curl.exe children on Windows
        taskkill //IM curl.exe //F >/dev/null 2>&1
        sleep 3
        pid=$(start_extractor)
    fi
done
