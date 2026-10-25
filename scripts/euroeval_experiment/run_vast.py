"""
run_vast.py — Vast.ai orchestration for EuroEval tier-split preparation.

  1. Reads VASTAI_API_KEY from .env
  2. Finds cheapest CPU instance with 128 GB RAM + 500 GB disk
  3. Rents the instance
  4. Uploads data/normalized/ (~55 GB) + prepare_tier_splits.py via rsync
  5. Runs two passes: index (Pass 1) then write (Pass 2) — stays in ~10 GB RAM
  6. Polls every 5 min until sentinel file appears
  7. Downloads the 6 tier-split JSONL files to tier_splits/
  8. Destroys the instance

Usage:
    python scripts/euroeval_experiment/run_vast.py

Requirements:
    pip install vastai
    ssh, rsync on PATH (Git Bash / WSL on Windows)
    VASTAI_API_KEY in F:/NederCorpus/.env
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT    = Path(__file__).resolve().parents[2]
NORM_DIR     = REPO_ROOT / "data" / "normalized"
SCRIPT_DIR   = Path(__file__).resolve().parent
OUT_DIR      = SCRIPT_DIR / "tier_splits"
REMOTE_BASE  = "/workspace"
TARGET_TOKENS = 8_000_000_000

POLL_INTERVAL = 300    # 5 minutes
MAX_WAIT      = 43200  # 12 hours hard timeout

SENTINEL      = f"{REMOTE_BASE}/splits_done.flag"
SPLIT_NAMES   = [
    "tier1_nl.jsonl", "tier1_all.jsonl",
    "tier2_nl.jsonl", "tier2_all.jsonl",
    "tier3_nl.jsonl", "tier3_all.jsonl",
]


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("VASTAI_API_KEY") or line.startswith("VAST_API_KEY"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key
    import os
    key = os.environ.get("VASTAI_API_KEY") or os.environ.get("VAST_API_KEY", "")
    if not key:
        sys.exit("ERROR: VASTAI_API_KEY not found in .env or environment.")
    return key


# ---------------------------------------------------------------------------
# SSH / rsync helpers
# ---------------------------------------------------------------------------

SSH_KEY = str(Path.home() / ".ssh" / "frysklaw_vastai")

SSH_OPTS = [
    "-i", SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ServerAliveInterval=60",
    "-o", "ConnectTimeout=30",
    "-o", "LogLevel=ERROR",
]


def ssh(host: str, port: int, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-p", str(port), f"root@{host}", cmd],
        capture_output=True, text=True, check=check,
    )


def rsync_up(host: str, port: int, local_dir: Path, remote_dir: str, max_retries: int = 5):
    """Upload via real rsync (not scp -r) so a dropped connection resumes instead of
    restarting a 59GB transfer from zero. This is what killed the original run —
    `scp -r` has no resume support and the connection dropped mid-transfer."""
    size_gb = sum(f.stat().st_size for f in local_dir.rglob("*") if f.is_file()) / 1e9
    print(f"  rsync {local_dir.name}/  ({size_gb:.1f} GB) -> {remote_dir} ...", flush=True)

    ssh_cmd = "ssh " + " ".join(SSH_OPTS) + f" -p {port}"
    for attempt in range(1, max_retries + 1):
        r = subprocess.run(
            ["rsync", "-az", "--partial", "--partial-dir=.rsync-partial",
             "--info=progress2",
             "-e", ssh_cmd,
             f"{local_dir}/",
             f"root@{host}:{remote_dir}/"],
        )
        if r.returncode == 0:
            print(f"  rsync complete (attempt {attempt}/{max_retries}).", flush=True)
            return
        print(f"  rsync attempt {attempt}/{max_retries} failed (exit {r.returncode}) "
              f"— retrying in 15s ...", flush=True)
        time.sleep(15)
    sys.exit(f"ERROR: rsync of {local_dir} failed after {max_retries} attempts. "
              f"Check network / instance disk space.")


def scp_up(host: str, port: int, local: Path, remote_dir: str):
    print(f"  scp  {local.name} ...", flush=True)
    subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port), str(local), f"root@{host}:{remote_dir}/"],
        check=True,
    )


def scp_down(host: str, port: int, remote_path: str, local_dir: Path):
    local_dir.mkdir(parents=True, exist_ok=True)
    name = Path(remote_path).name
    r = subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port),
         f"root@{host}:{remote_path}",
         str(local_dir / name)],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        size = (local_dir / name).stat().st_size / 1e9
        print(f"  OK {name}  ({size:.2f} GB)")
    else:
        print(f"  FAIL {name}: {r.stderr.strip()}")


def wait_for_ssh(host: str, port: int, vast=None, instance_id=None, timeout: int = 480):
    print("  Waiting for SSH ...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = ssh(host, port, "echo ok", check=False)
        if r.returncode == 0 and "ok" in r.stdout:
            print(" ready.")
            return
        print(".", end="", flush=True)
        time.sleep(15)
    if vast and instance_id:
        print(f"\n  Auto-destroying instance #{instance_id} ...")
        vast.destroy_instance(id=instance_id)
    sys.exit("\nERROR: SSH never became available. Instance destroyed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--resume-host",        default=None, help="Skip rent; use existing SSH host")
    p.add_argument("--resume-port",        type=int, default=None)
    p.add_argument("--resume-instance-id", type=int, default=None)
    return p.parse_args()


def main():
    args = parse_args()

    if not NORM_DIR.exists():
        sys.exit(f"ERROR: {NORM_DIR} not found -- run pipeline first.")

    api_key = load_api_key()
    from vastai import VastAI
    vast = VastAI(api_key=api_key)
    print("vast.ai authenticated.")

    if args.resume_host:
        # Resume mode: skip find/rent, connect to existing instance
        ssh_host    = args.resume_host
        ssh_port    = args.resume_port or 22
        instance_id = args.resume_instance_id
        print(f"Resuming instance #{instance_id} at {ssh_host}:{ssh_port}")
        wait_for_ssh(ssh_host, ssh_port, vast=vast, instance_id=instance_id)
    else:
        # Find cheapest instance with enough RAM + disk
        print("\nSearching for cheapest CPU instance (128 GB RAM, 500 GB disk) ...")
        offers = vast.search_offers(
            query="cpu_ram>=128 disk_space>=500 inet_up>=300",
            order="dph_total",
            limit=10,
        )
        if not offers:
            sys.exit("ERROR: no offers match criteria. Try relaxing constraints.")

        best = offers[0]
        offer_id  = best["id"]
        gpu_name  = best.get("gpu_name") or "CPU-only"
        cpu_cores = best.get("cpu_cores", "?")
        cpu_ram   = best.get("cpu_ram", "?")
        disk      = best.get("disk_space", "?")
        rate      = best.get("dph_total", 0)
        print(f"  #{offer_id}  {gpu_name}  {cpu_cores} cores / {cpu_ram} GB RAM / "
              f"{disk} GB disk  ${rate:.3f}/hr")
        est_cost = rate * 8
        print(f"  Estimated cost: ~${est_cost:.2f} for 8h run")

        # Rent
        print("\nRenting instance ...")
        result = vast.create_instance(
            id=offer_id,
            image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime",
            disk=500,
            label="nedercorpus-euroeval-tier-splits",
            onstart_cmd="pip install -q python-dotenv && "
                         "(apt-get update -qq && apt-get install -qq -y rsync) || true",
        )
        if not result or not isinstance(result, dict):
            sys.exit(f"ERROR: create_instance failed: {result}")

        instance_id = result.get("new_contract") or result.get("id")
        if not instance_id:
            sys.exit(f"ERROR: could not get instance ID from: {result}")
        print(f"  Instance #{instance_id} created.")

        # Wait for running
        print("\nWaiting for instance to start ...")
        ssh_host = ssh_port = None
        for _ in range(60):
            time.sleep(15)
            instances = vast.show_instances()
            if not isinstance(instances, list):
                continue
            inst = next((i for i in instances if i.get("id") == instance_id), None)
            if inst and inst.get("actual_status") == "running":
                ssh_host = inst.get("ssh_host") or inst.get("public_ipaddr", "")
                ssh_port = int(inst.get("ssh_port", 22))
                print(f"  Running -- {ssh_host}:{ssh_port}")
                break
        else:
            vast.destroy_instance(id=instance_id)
            sys.exit("ERROR: instance never reached running state. Destroyed.")

        wait_for_ssh(ssh_host, ssh_port, vast=vast, instance_id=instance_id)

    # Create remote dirs
    ssh(ssh_host, ssh_port,
        f"mkdir -p {REMOTE_BASE}/data/normalized "
        f"{REMOTE_BASE}/scripts/euroeval_experiment/tier_splits")

    # Ensure rsync is present remotely (onstart_cmd install may still be running)
    print("\nEnsuring rsync is installed remotely ...")
    for _ in range(20):
        r = ssh(ssh_host, ssh_port, "command -v rsync", check=False)
        if r.returncode == 0 and r.stdout.strip():
            print("  rsync ready.")
            break
        ssh(ssh_host, ssh_port,
            "apt-get update -qq && apt-get install -qq -y rsync", check=False)
        time.sleep(10)
    else:
        sys.exit("ERROR: could not install rsync on remote instance.")

    # Upload Pass 1: normalized data (~55 GB via rsync, compressed)
    print("\nUploading normalized data (Pass 1 of upload — this takes ~10-30 min) ...")
    rsync_up(ssh_host, ssh_port, NORM_DIR, f"{REMOTE_BASE}/data/normalized")

    # Upload script
    print("\nUploading prepare_tier_splits.py ...")
    ssh(ssh_host, ssh_port, f"mkdir -p {REMOTE_BASE}/scripts/euroeval_experiment")
    scp_up(ssh_host, ssh_port,
           SCRIPT_DIR / "prepare_tier_splits.py",
           f"{REMOTE_BASE}/scripts/euroeval_experiment")

    # Launch — Pass 1 (index) then Pass 2 (write per split), write sentinel when done
    print("\nLaunching tier-split preparation (T1->T2: index then write) ...")
    remote_cmd = (
        f"cd {REMOTE_BASE} && "
        "nohup bash -c '"
        f"python scripts/euroeval_experiment/prepare_tier_splits.py "
        f"  --norm_dir {REMOTE_BASE}/data/normalized "
        f"  --out_dir  {REMOTE_BASE}/scripts/euroeval_experiment/tier_splits "
        f"  --target_tokens {TARGET_TOKENS} "
        f"> {REMOTE_BASE}/splits.log 2>&1 "
        f"&& touch {SENTINEL}"
        f"' > {REMOTE_BASE}/nohup.out 2>&1 & echo $!"
    )
    r = ssh(ssh_host, ssh_port, remote_cmd)
    pid = r.stdout.strip()
    print(f"  PID: {pid}")

    # Poll
    print(f"\nPolling every {POLL_INTERVAL//60} min (max {MAX_WAIT//3600}h) ...")
    start = time.time()
    done  = False
    while time.time() - start < MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        elapsed = (time.time() - start) / 3600

        check = ssh(ssh_host, ssh_port,
                    f"test -f {SENTINEL} && echo DONE || echo RUNNING",
                    check=False)
        status = check.stdout.strip()

        log = ssh(ssh_host, ssh_port,
                  f"tail -2 {REMOTE_BASE}/splits.log 2>/dev/null || echo '(waiting)'",
                  check=False)
        last = log.stdout.strip().replace("\n", " | ")

        print(f"  [{elapsed:.1f}h] {status} | {last}", flush=True)

        if status == "DONE":
            done = True
            break

    if not done:
        print("WARNING: max wait exceeded — downloading whatever exists.")

    # Download tier splits
    print("\nDownloading tier-split files ...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in SPLIT_NAMES:
        scp_down(ssh_host, ssh_port,
                 f"{REMOTE_BASE}/scripts/euroeval_experiment/tier_splits/{name}",
                 OUT_DIR)

    # Download log
    scp_down(ssh_host, ssh_port, f"{REMOTE_BASE}/splits.log", SCRIPT_DIR)

    # Destroy
    print(f"\nDestroying instance #{instance_id} ...")
    vast.destroy_instance(id=instance_id)
    print("  Done. Instance destroyed.")
    print(f"\nTier splits written to: {OUT_DIR}/")


if __name__ == "__main__":
    main()
