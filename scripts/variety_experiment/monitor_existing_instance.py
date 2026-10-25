"""
monitor_existing_instance.py — Reattach to an already-running vast.ai
instance (no create_instance call) and poll it until the multi-seed
replication's results_multiseed.json appears, then download results and
destroy the instance.

Written because run_vast.py's own poll loop bakes MAX_WAIT in at process
start, and the original 120h budget turned out too short once real per-seed
timings were measured (~56.5h/seed observed on the actual instance, ~126h
remaining for seeds 42's last condition + all of 43/44) — restarting
run_vast.py from scratch would have created a NEW instance and re-uploaded
everything instead of reattaching to the one already most of the way done.

Usage:
    python monitor_existing_instance.py --host ssh2.vast.ai --port 19412 --instance_id 46539413
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "experiment_results"
REMOTE_BASE = "/workspace"
SEEDS = [42, 43, 44]

SSH_KEY = str(Path.home() / ".ssh" / "id_rsa")
SSH_OPTS = [
    "-i", SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ServerAliveInterval=30",
    "-o", "ConnectTimeout=30",
    "-o", "LogLevel=ERROR",
]

FILES_TO_DOWNLOAD = [
    "experiment_data/results_multiseed.md",
    "experiment_data/results_multiseed.json",
    "logs_prepare_gptnl_af_fy.txt",
    *[f"logs_nl_only_seed{s}.txt" for s in SEEDS],
    *[f"logs_full_seed{s}.txt" for s in SEEDS],
    *[f"logs_gptnl_af_fy_seed{s}.txt" for s in SEEDS],
    "logs_eval_multiseed.txt",
]

POLL_INTERVAL = 900   # 15 min — this is a long-haul monitor, no need to poll often
# No hard timeout — this script only stops when results_multiseed.json exists,
# or when manually interrupted. The instance is never destroyed except on
# genuine completion.


def ssh(host: str, port: int, cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-p", str(port), f"root@{host}", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def scp_down(host: str, port: int, remote_path: str, local_dir: Path):
    local_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port),
         f"root@{host}:{REMOTE_BASE}/{remote_path}",
         str(local_dir / Path(remote_path).name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode == 0:
        print(f"  [OK] {remote_path}")
    else:
        print(f"  [MISSING] {remote_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--instance_id", type=int, required=True)
    ap.add_argument("--destroy_on_complete", action="store_true", default=True)
    args = ap.parse_args()

    print(f"Reattached monitor for instance #{args.instance_id} ({args.host}:{args.port}).")
    print(f"Polling every {POLL_INTERVAL//60} min. No timeout — runs until "
          f"results_multiseed.json appears or this script is stopped.\n")

    start = time.time()
    while True:
        time.sleep(POLL_INTERVAL)
        elapsed_h = (time.time() - start) / 3600

        # A transient SSH/network hiccup (laptop sleep, brief connectivity
        # loss, etc.) must not kill this long-haul monitor — the previous
        # unguarded version died silently after ~49h with no traceback,
        # leaving the instance unmonitored (though still training fine via
        # nohup, since remote training is independent of this process).
        try:
            check = ssh(args.host, args.port,
                        f"test -f {REMOTE_BASE}/experiment_data/results_multiseed.json "
                        f"&& echo DONE || echo RUNNING")
            status = check.stdout.strip()

            # Report progress from whichever condition's log was touched most recently.
            prog = ssh(args.host, args.port,
                        f"cd {REMOTE_BASE} && ls -t logs_*.txt 2>/dev/null | head -1 | "
                        f"xargs -I{{}} sh -c 'echo {{}}: $(tail -c 120 {{}} | tr -d \"\\r\")'")
        except Exception as e:
            print(f"  [{elapsed_h:.1f}h] POLL ERROR (will retry next cycle): {e}", flush=True)
            continue
        print(f"  [{elapsed_h:.1f}h] {status} | {prog.stdout.strip()}", flush=True)

        if status == "DONE":
            print("\nresults_multiseed.json found — downloading results …")
            for remote_path in FILES_TO_DOWNLOAD:
                scp_down(args.host, args.port, remote_path, RESULTS_DIR)

            results_md = RESULTS_DIR / "results_multiseed.md"
            if results_md.exists():
                print("\n" + "=" * 60)
                print(results_md.read_text(encoding="utf-8"))
                print("=" * 60)

            if args.destroy_on_complete:
                print(f"\nDestroying instance #{args.instance_id} (run complete) …")
                from vastai import VastAI
                api_key = None
                for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("VASTAI_API_KEY") or line.startswith("VAST_API_KEY"):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                VastAI(api_key=api_key).destroy_instance(id=args.instance_id)
                print("  Done.")
            break


if __name__ == "__main__":
    main()
