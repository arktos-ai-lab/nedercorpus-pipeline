"""
run_vast.py — End-to-end vast.ai orchestration for the multi-variety experiment.

  1. Reads VAST_API_KEY from .env
  2. Finds cheapest suitable GPU offer
  3. Rents the instance
  4. Uploads experiment data + scripts via scp
  5. Starts training with nohup (survives SSH disconnect)
  6. Polls every 10 min until results.json appears
  7. Downloads results.md + results.json + training logs
  8. Destroys the instance

Usage:
    python scripts/variety_experiment/run_vast.py

Requirements:
    pip install vastai python-dotenv
    ssh and scp on PATH (Git Bash / WSL on Windows)
"""

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
DATA_DIR     = REPO_ROOT / "experiment_data"
NORM_DIR     = REPO_ROOT / "data" / "normalized"
SCRIPT_DIR   = Path(__file__).resolve().parent
RESULTS_DIR  = REPO_ROOT / "experiment_results"
REMOTE_BASE  = "/workspace"

SEEDS = [42, 43, 44]
EUROEVAL_SCRIPT_DIR = REPO_ROOT / "scripts" / "euroeval_experiment"

FILES_TO_UPLOAD = [
    (DATA_DIR   / "holdout.jsonl",              f"{REMOTE_BASE}/experiment_data/"),
    (DATA_DIR   / "train_nl_only.jsonl",        f"{REMOTE_BASE}/experiment_data/"),
    (DATA_DIR   / "train_full.jsonl",           f"{REMOTE_BASE}/experiment_data/"),
    (DATA_DIR   / "train_gptnl_af_fy.jsonl",    f"{REMOTE_BASE}/experiment_data/"),
    (SCRIPT_DIR / "train_lm.py",                      f"{REMOTE_BASE}/scripts/variety_experiment/"),
    (SCRIPT_DIR / "eval_ppl_multiseed.py",            f"{REMOTE_BASE}/scripts/variety_experiment/"),
    (EUROEVAL_SCRIPT_DIR / "train_tier.py",           f"{REMOTE_BASE}/scripts/euroeval_experiment/"),
]
# train_gptnl_af_fy.jsonl is built LOCALLY (prepare_gptnl_af_fy_split.py needs
# data/normalized, ~56GB — the first attempt at this run tried to rsync that
# whole directory to the remote and, at this connection's observed upload
# speed (~0.33 MB/s), the transfer alone would have taken ~47h, most of the
# 65h budget, before any training even started. Building the split locally
# (we already have data/normalized on disk) and uploading only the ~GB-scale
# result avoids that entirely.

FILES_TO_DOWNLOAD = [
    "experiment_data/results_multiseed.md",
    "experiment_data/results_multiseed.json",
    "logs_prepare_gptnl_af_fy.txt",
    *[f"logs_nl_only_seed{s}.txt" for s in SEEDS],
    *[f"logs_full_seed{s}.txt" for s in SEEDS],
    *[f"logs_gptnl_af_fy_seed{s}.txt" for s in SEEDS],
    "logs_eval_multiseed.txt",
]

POLL_INTERVAL = 600    # 10 minutes
MAX_WAIT      = 432000 # 120 hours hard timeout. At local's measured ~1.1 it/s (batch=16),
                        # 6 tier runs (~8.5h each) + 3 gptnl_af_fy runs (~15.4h each, 2B vs
                        # 1.1B token budget) ~= 97h total — 65h wasn't enough even before
                        # accounting for a rented card being slower than the local 5090.

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
# SSH / SCP helpers (subprocess — works on Git Bash / WSL / Linux / Mac)
# ---------------------------------------------------------------------------

SSH_KEY = str(Path.home() / ".ssh" / "id_rsa")

SSH_OPTS = [
    "-i", SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ServerAliveInterval=30",
    "-o", "ConnectTimeout=30",
    "-o", "LogLevel=ERROR",
]


def ssh(host: str, port: int, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    # encoding="utf-8" is required here — subprocess.run's text=True otherwise
    # decodes with the platform-preferred encoding, which on Windows is
    # typically cp1252, not UTF-8. Remote training output routinely contains
    # UTF-8 multi-byte characters (the "…" in progress messages, etc.) that
    # cp1252 cannot decode, raising UnicodeDecodeError. This crashed the poll
    # loop mid-run and, via the provisioning try/except, destroyed a healthy,
    # actively-training instance — the safety-net doing exactly what it was
    # built to do, just triggered by an encoding bug rather than a real failure.
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-p", str(port), f"root@{host}", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def scp_up(host: str, port: int, local: Path, remote_dir: str):
    size_gb = local.stat().st_size / 1e9
    print(f"  Uploading {local.name:35s} {size_gb:.2f} GB …", flush=True)
    subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port), str(local), f"root@{host}:{remote_dir}/"],
        check=True,
    )


def rsync_up_dir(host: str, port: int, local_dir: Path, remote_dir: str, max_retries: int = 5):
    """Resumable directory upload — data/normalized/ is tens of GB, and a plain
    scp -r has no resume support (this exact failure mode killed the euroeval
    experiment's first upload attempt: one dropped connection, restart from zero)."""
    size_gb = sum(f.stat().st_size for f in local_dir.rglob("*") if f.is_file()) / 1e9
    print(f"  rsync {local_dir.name}/  ({size_gb:.1f} GB) -> {remote_dir} ...", flush=True)
    ssh_cmd = "ssh " + " ".join(SSH_OPTS) + f" -p {port}"
    for attempt in range(1, max_retries + 1):
        r = subprocess.run(
            ["rsync", "-az", "--partial", "--partial-dir=.rsync-partial",
             "--info=progress2", "-e", ssh_cmd,
             f"{local_dir}/", f"root@{host}:{remote_dir}/"],
        )
        if r.returncode == 0:
            print(f"  rsync complete (attempt {attempt}/{max_retries}).", flush=True)
            return
        print(f"  rsync attempt {attempt}/{max_retries} failed (exit {r.returncode}) "
              f"— retrying in 15s ...", flush=True)
        time.sleep(15)
    sys.exit(f"ERROR: rsync of {local_dir} failed after {max_retries} attempts.")


def scp_down(host: str, port: int, remote_path: str, local_dir: Path):
    local_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port),
         f"root@{host}:{REMOTE_BASE}/{remote_path}",
         str(local_dir / Path(remote_path).name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode == 0:
        print(f"  ✓ {remote_path}")
    else:
        print(f"  ✗ {remote_path}: {r.stderr.strip()}")


def wait_for_ssh(host: str, port: int, timeout: int = 300):
    print("  Waiting for SSH …", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = ssh(host, port, "echo ok", check=False)
        if r.returncode == 0 and "ok" in r.stdout:
            print(" ready.")
            return
        print(".", end="", flush=True)
        time.sleep(10)
    raise RuntimeError("SSH never became available.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Verify local files
    for local, _ in FILES_TO_UPLOAD:
        if not local.exists():
            sys.exit(f"ERROR: {local} not found — run prepare_splits.py first.")

    # Auth
    api_key = load_api_key()
    from vastai import VastAI
    vast = VastAI(api_key=api_key)
    print("vast.ai authenticated.")

    # Find cheapest offer with enough VRAM to run BATCH_SIZE=16 (the
    # 24GB-tuned default) rather than the batch=4 fallback. A 12GB card
    # (RTX 3060) forced batch=4 on the previous run and measured ~2.4s/it
    # vs local's ~0.9s/it at batch=16 — a ~2.6x slowdown that would have
    # blown the 65h timeout without finishing even one condition. Local's
    # 24GB card uses ~9.6-10GB at batch=16, so >=16GB leaves safe headroom.
    print("\nSearching for cheapest offer with >=16GB VRAM …")
    offers = vast.search_offers(
        query="gpu_ram>=16 num_gpus=1 cuda_vers>=12.0 inet_up>=200 disk_space>=50",
        order="dph_total",
        limit=10,
    )
    if not offers:
        sys.exit("ERROR: no offers match the criteria.")

    best = offers[0]
    offer_id = best["id"]
    print(f"  #{offer_id}  {best.get('gpu_name','?')}  "
          f"{best.get('gpu_ram','?')}GB VRAM  "
          f"${best.get('dph_total', 0):.3f}/hr")
    est_cost = best.get("dph_total", 0) * 65
    print(f"  Estimated cost: ~${est_cost:.2f} for ~65h (3 seeds x 3 conditions, worst case)")

    # Rent
    print("\nRenting instance …")
    result = vast.create_instance(
        id=offer_id,
        image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime",
        disk=150,  # data/normalized (~55GB) + 3 conditions worth of checkpoints
        label="nedercorpus-variety-experiment",
        # The base image ships torch 2.4.0, but transformers/datasets/accelerate
        # pull latest by default, which needs a newer torch (surfaced as
        # `ImportError: cannot import name 'DTensor'`). Upgrading torch alone
        # then breaks torchvision/torchaudio's compiled extensions against the
        # new ABI (`operator torchvision::nms does not exist`), so all three
        # must move together. All discovered the hard way debugging run #3.
        onstart_cmd="pip install -q --upgrade torch torchvision torchaudio "
                     "--index-url https://download.pytorch.org/whl/cu124 && "
                     "pip install -q transformers datasets accelerate && "
                     "(apt-get update -qq && apt-get install -qq -y rsync) || true",
    )
    if not result or not isinstance(result, dict):
        sys.exit(f"ERROR: create_instance failed: {result}")

    instance_id = result.get("new_contract") or result.get("id")
    if not instance_id:
        sys.exit(f"ERROR: could not get instance ID from: {result}")
    print(f"  Instance #{instance_id} created.")

    # Attach SSH key — create_instance() does NOT do this on its own; without
    # it every SSH attempt below fails with "Permission denied (publickey)"
    # no matter which local key is used, and the instance is left running
    # with nothing using it (this bit us on run #46384967).
    pubkey_path = Path(SSH_KEY + ".pub")
    if not pubkey_path.exists():
        vast.destroy_instance(id=instance_id)
        sys.exit(f"ERROR: {pubkey_path} not found — destroyed orphaned instance #{instance_id}.")
    pubkey = pubkey_path.read_text(encoding="utf-8").strip()
    attach = vast.attach_ssh(instance_id=instance_id, ssh_key=pubkey)
    print(f"  SSH key attached: {attach}")

    # Everything from here on can fail mid-way (bad host, dropped SSH, OOM,
    # timeout). Any such failure must still destroy the instance — an
    # unreachable or crashed instance left running is exactly the "idle
    # instance burning money" failure mode we're guarding against.
    try:
        # Wait for running state
        print("\nWaiting for instance to start …")
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
                print(f"  Running — {ssh_host}:{ssh_port}")
                break
        else:
            raise RuntimeError("instance never reached running state.")

        # SSH ready
        wait_for_ssh(ssh_host, ssh_port)

        # Prepare remote dirs
        ssh(ssh_host, ssh_port,
            f"mkdir -p {REMOTE_BASE}/experiment_data {REMOTE_BASE}/scripts/variety_experiment "
            f"{REMOTE_BASE}/scripts/euroeval_experiment {REMOTE_BASE}/data/normalized")

        # Upload files via scp — train_gptnl_af_fy.jsonl is built locally
        # (see FILES_TO_UPLOAD comment) so no rsync of data/normalized is
        # needed at all.
        print("\nUploading data and scripts …")
        for local, remote_dir in FILES_TO_UPLOAD:
            scp_up(ssh_host, ssh_port, local, remote_dir)

        # train_lm.py's BATCH_SIZE=16 is tuned for 24GB local GPUs and uses
        # ~9.6-10GB in practice. Only fall back to batch=4 (train_tier.py's
        # already-safe default) if the actual rented card has less than
        # 16000MiB VRAM — forcing batch=4 unconditionally on a card that
        # didn't need it cost a ~2.6x slowdown last run (measured ~2.4s/it
        # vs local's ~0.9s/it), enough to blow the 65h timeout outright.
        vram_check = ssh(ssh_host, ssh_port,
                          "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits")
        vram_mib = int(vram_check.stdout.strip().splitlines()[0])
        print(f"\nDetected {vram_mib} MiB VRAM on rented instance.")
        if vram_mib < 16000:
            print("  <16000 MiB — patching train_lm.py to batch=4/grad_accum=8 …")
            ssh(ssh_host, ssh_port,
                f"cd {REMOTE_BASE} && "
                "sed -i 's/^BATCH_SIZE    = 16.*/BATCH_SIZE    = 4    "
                "# reduced for rented GPU VRAM (patched by run_vast.py)/' "
                "scripts/variety_experiment/train_lm.py && "
                "sed -i 's/^GRAD_ACCUM    = 2.*/GRAD_ACCUM    = 8    "
                "# effective batch 32, unchanged (patched by run_vast.py)/' "
                "scripts/variety_experiment/train_lm.py")
        else:
            print("  >=16000 MiB — keeping default batch=16 (matches local speed).")

        # Launch training (nohup — safe if terminal closes)
        # 3 seeds x 3 conditions, sequential, then one multi-seed eval pass.
        print("\nLaunching training (3 seeds x 3 conditions) …")
        train_cmds = []
        for seed in SEEDS:
            for split in ("nl_only", "full"):
                train_cmds.append(
                    f"python scripts/variety_experiment/train_lm.py "
                    f"--split {split} --seed {seed} --out_dir experiment_data "
                    f"> logs_{split}_seed{seed}.txt 2>&1"
                )
            train_cmds.append(
                f"python scripts/euroeval_experiment/train_tier.py "
                f"--input experiment_data/train_gptnl_af_fy.jsonl --label gptnl_af_fy "
                f"--seed {seed} --out_dir experiment_data --max_tokens 2000000000 "
                f"> logs_gptnl_af_fy_seed{seed}.txt 2>&1"
            )
        seeds_arg = " ".join(str(s) for s in SEEDS)
        train_cmds.append(
            f"python scripts/variety_experiment/eval_ppl_multiseed.py "
            f"--out_dir experiment_data --seeds {seeds_arg} "
            f"--splits nl_only full gptnl_af_fy "
            f"> logs_eval_multiseed.txt 2>&1"
        )
        train_script = (
            f"cd {REMOTE_BASE} && "
            "nohup bash -c '" + " && ".join(train_cmds) + "'"
            " > nohup.out 2>&1 & echo $!"
        )
        r = ssh(ssh_host, ssh_port, train_script)
        pid = r.stdout.strip()
        print(f"  Training PID: {pid}")

        # Poll for completion
        print(f"\nPolling every {POLL_INTERVAL//60} min (max {MAX_WAIT//3600}h) …")
        start = time.time()
        done = False
        while time.time() - start < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            elapsed = (time.time() - start) / 3600

            check = ssh(ssh_host, ssh_port,
                        f"test -f {REMOTE_BASE}/experiment_data/results_multiseed.json "
                        f"&& echo DONE || echo RUNNING",
                        check=False)
            status = check.stdout.strip()

            log = ssh(ssh_host, ssh_port,
                      f"tail -1 {REMOTE_BASE}/logs_full_seed{SEEDS[-1]}.txt 2>/dev/null || "
                      f"tail -1 {REMOTE_BASE}/logs_nl_only_seed{SEEDS[0]}.txt 2>/dev/null || "
                      "echo '(waiting)'",
                      check=False)
            last_line = log.stdout.strip()

            print(f"  [{elapsed:.1f}h] {status} | {last_line}", flush=True)

            if status == "DONE":
                done = True
                break

        if not done:
            print("WARNING: timeout — downloading partial results.")

        # Download results
        print("\nDownloading results …")
        for remote_path in FILES_TO_DOWNLOAD:
            scp_down(ssh_host, ssh_port, remote_path, RESULTS_DIR)

    except Exception as e:
        print(f"\nERROR during provisioning/training: {e}")
        print(f"Destroying instance #{instance_id} to avoid idle billing …")
        vast.destroy_instance(id=instance_id)
        print("  Done. Instance destroyed.")
        sys.exit(1)

    results_md = RESULTS_DIR / "results_multiseed.md"
    if results_md.exists():
        print("\n" + "=" * 60)
        print(results_md.read_text(encoding="utf-8"))
        print("=" * 60)

    # Destroy
    print(f"\nDestroying instance #{instance_id} …")
    vast.destroy_instance(id=instance_id)
    print("  Done. Instance destroyed.")
    print(f"\nResults saved to: {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
