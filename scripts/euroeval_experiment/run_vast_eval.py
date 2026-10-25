"""
run_vast_eval.py — Run real EuroEval Dutch-benchmark evaluation against the
8 already-trained local checkpoints (tier1_nl/all, tier2_nl/all, tier3_nl/all,
gptnl_1b, gptnl_8b) on a rented vast.ai Linux GPU instance.

Why: EuroEval's generative-model path requires vllm, which needs
cudaHostRegister (pinned memory) — genuinely unsupported on WSL2 (confirmed
via NVIDIA's own forums: "cudaHostRegister not supported on WSL2"), not a
patchable bug. A real Linux box sidesteps this entirely. Checkpoints are
small (448MB each, ~3.6GB total) and this is inference-only, so it's a
short, cheap rental — not the multi-day training runs.

Usage:
    python run_vast_eval.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]
CKPT_DIR = REPO_ROOT / "scripts" / "euroeval_experiment" / "checkpoints"
RESULTS_DIR = REPO_ROOT / "experiment_results" / "euroeval_vast"
REMOTE_BASE = "/workspace"

CONDITIONS = ["tier1_nl", "tier1_all", "tier2_nl", "tier2_all",
              "tier3_nl", "tier3_all", "gptnl_1b", "gptnl_8b"]
SEED = 42

SSH_KEY = str(Path.home() / ".ssh" / "id_rsa")
SSH_OPTS = [
    "-i", SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ServerAliveInterval=30",
    "-o", "ConnectTimeout=30",
    "-o", "LogLevel=ERROR",
]

POLL_INTERVAL = 120
MAX_WAIT = 21600  # 6h — generous for 8 short eval runs, still bounded since this is cheap/fast


def ssh(host, port, cmd, check=False):
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-p", str(port), f"root@{host}", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=check,
    )


def scp_up_dir(host, port, local_dir, remote_dir):
    subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port), "-r", str(local_dir), f"root@{host}:{remote_dir}/"],
        check=True,
    )


def scp_down(host, port, remote_path, local_dir):
    local_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["scp", *SSH_OPTS, "-P", str(port),
         f"root@{host}:{REMOTE_BASE}/{remote_path}", str(local_dir / Path(remote_path).name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(f"  {'[OK]' if r.returncode == 0 else '[MISSING]'} {remote_path}")


def load_api_key():
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("VASTAI_API_KEY") or line.startswith("VAST_API_KEY"):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    sys.exit("ERROR: VASTAI_API_KEY not found in .env")


def main():
    for cond in CONDITIONS:
        ckpt = CKPT_DIR / f"checkpoint_{cond}_seed{SEED}" / "final"
        if not ckpt.exists():
            sys.exit(f"ERROR: {ckpt} not found — train all 8 conditions first.")

    api_key = load_api_key()
    from vastai import VastAI
    vast = VastAI(api_key=api_key)
    print("vast.ai authenticated.")

    # The real problem underlying every driver/CUDA-runtime error so far:
    # "cheapest GPU" kept landing on Pascal-generation cards (Titan Xp, GTX
    # 1070 Ti, GTX 1080 — all confirmed rented across earlier attempts).
    # Confirmed via explicit torch warning: GTX 1080 is compute capability
    # 6.1 (sm_61), but this torch build only supports sm_75 and up (Turing/
    # 2018+) — no CUDA/driver version fixes that, it's a hardware ceiling.
    # compute_cap>=750 (sm_75) filters to actually-supported GPUs directly
    # — more precise than the cuda_vers proxy tried previously, which still
    # let a GTX 1080 through since its driver alone could report cuda_vers
    # >=13 independent of the GPU's own compute capability.
    print("\nSearching for cheapest offer with a real CUDA devel image (nvcc built-in) …")
    offers = vast.search_offers(
        # compute_cap and cuda_vers (actual installed driver) are
        # independent — confirmed on an RTX 3070 laptop (compute_cap 8.6,
        # well above 750) that still had the old driver-mismatch error.
        # Modern GPU hardware doesn't guarantee a modern driver was
        # actually installed by the host operator. Both filters together.
        query="gpu_ram>=8 num_gpus=1 compute_cap>=750 cuda_vers>=13.0 inet_up>=200 disk_space>=30",
        order="dph_total", limit=10,
    )
    if not offers:
        sys.exit("ERROR: no offers match.")
    best = offers[0]
    offer_id = best["id"]
    print(f"  #{offer_id}  {best.get('gpu_name','?')}  {best.get('gpu_ram','?')}GB VRAM  "
          f"${best.get('dph_total', 0):.3f}/hr")

    print("\nRenting instance …")
    result = vast.create_instance(
        id=offer_id,
        # ubuntu22.04's default python3 is 3.10, which caps euroeval at the
        # 15.x line (16.x needs >=3.11, 17.x needs >=3.12) — and 15.16.0's
        # current PyPI dependency set is broken (confirmed: both the
        # unpinned install and a manual vllm==0.26.0 pin each hit a
        # different live import error). Switching the whole base image to
        # ubuntu24.04 (for its default python3.12) failed to even reach
        # "running" state 4 times in a row across two different GPU
        # offers — that image is apparently not reliably pullable on
        # vast.ai's host fleet. Back to the known-reliable ubuntu22.04
        # image; installing python3.12 via deadsnakes instead, matching
        # the WSL2 setup (python3.12 + euroeval 17.7.0 + vllm 0.26.0) that
        # actually imported cleanly, without gambling on a whole new image.
        image="nvidia/cuda:12.4.1-devel-ubuntu22.04",
        disk=40,
        label="nedercorpus-euroeval-vast",
        # The deadsnakes-PPA approach (apt-get update + add-apt-repository +
        # more apt-get) failed on two different hosts, stuck 20+ minutes
        # without finishing either time — apt's PPA path depends on
        # reaching a GPG keyserver, which is a common thing for rented GPU
        # clouds to block or rate-limit. Switching to `uv` (astral.sh):
        # installs via one curl'd shell script (no apt/keyserver involved),
        # then downloads a prebuilt standalone Python 3.12 directly from
        # GitHub releases — a very different, likely more reachable, network
        # path than an Ubuntu keyserver. rsync was never actually used
        # anywhere in this script (only scp), so it's dropped too.
        onstart_cmd=(
            "curl -LsSf https://astral.sh/uv/install.sh | sh && "
            "export PATH=\"$HOME/.local/bin:$PATH\" && "
            "uv python install 3.12 && "
            # `uv python install` doesn't put a bare `python3.12` on PATH —
            # an explicit venv gives a fixed, unambiguous binary path
            # (/root/venv312/bin/python) for every later step to reference.
            "uv venv --python 3.12 /root/venv312 && "
            "touch /root/setup_done"
        ),
    )
    if not result or not isinstance(result, dict):
        sys.exit(f"ERROR: create_instance failed: {result}")
    instance_id = result.get("new_contract") or result.get("id")
    print(f"  Instance #{instance_id} created.")

    pubkey_path = Path(SSH_KEY + ".pub")
    ssh_host = ssh_port = None
    try:
        print("\nWaiting for instance to start …")
        # 3 consecutive "never reached running state" failures started
        # exactly when the image switched to ubuntu24.04 — likely a larger,
        # less commonly cached devel image needing longer than 15min to
        # pull fresh on some hosts, not pure bad luck. Doubled to 30min.
        for _ in range(120):
            time.sleep(15)
            insts = vast.show_instances()
            if not isinstance(insts, list):
                continue
            inst = next((i for i in insts if i.get("id") == instance_id), None)
            if inst and inst.get("actual_status") == "running":
                ssh_host = inst.get("ssh_host") or inst.get("public_ipaddr", "")
                ssh_port = int(inst.get("ssh_port", 22))
                print(f"  Running — {ssh_host}:{ssh_port}")
                break
        else:
            raise RuntimeError("instance never reached running state.")

        if pubkey_path.exists():
            vast.attach_ssh(instance_id=instance_id, ssh_key=pubkey_path.read_text(encoding="utf-8").strip())

        print("\nWaiting for basic SSH …", end="", flush=True)
        # Phase 1: just confirm the container/sshd is up at all. This alone
        # should be fast — conflating it with "is onstart_cmd done" (as the
        # previous version did, checking for python3.12 directly) made a
        # slow-SSH host indistinguishable from a slow-setup host, and each
        # individual failed ssh() call itself burns up to 30s (ConnectTimeout)
        # on top of the sleep, silently eating the budget.
        deadline = time.time() + 300
        while time.time() < deadline:
            r = ssh(ssh_host, ssh_port, "echo ok", check=False)
            if r.returncode == 0 and "ok" in r.stdout:
                print(" ready.")
                break
            print(".", end="", flush=True)
            time.sleep(10)
        else:
            raise RuntimeError("basic SSH never became available.")

        print("Waiting for onstart_cmd (uv + python3.12 + venv) to finish …",
              end="", flush=True)
        # Phase 2: uv downloads a prebuilt Python binary + creates a venv —
        # should be a couple minutes, not the 20+ min the apt/PPA path took.
        # Shorter budget gives faster feedback if this path has its own
        # problems, while still leaving real margin over the expected time.
        deadline = time.time() + 600
        while time.time() < deadline:
            r = ssh(ssh_host, ssh_port, "test -f /root/setup_done && echo ok", check=False)
            if r.returncode == 0 and "ok" in r.stdout:
                print(" ready.")
                break
            print(".", end="", flush=True)
            time.sleep(15)
        else:
            raise RuntimeError("SSH/setup never became available.")

        # Pin euroeval itself to 17.7.0 (the version confirmed importable —
        # got past every Python-level import — in the earlier WSL2 attempt,
        # which only failed on a WSL2-specific pinned-memory limitation, not
        # a dependency issue). Letting pip's resolver pick a fully
        # mutually-compatible dependency set from that anchor is more
        # reliable than pinning individual sub-dependencies by hand — the
        # previous attempt's manual `vllm==0.26.0` pin fixed one import
        # error but broke huggingface_hub compatibility instead.
        print("\nInstalling euroeval[generative]==17.7.0 (pinned — latest unpinned had "
              "two different dependency-drift breaks across the last two attempts) …")
        r = ssh(ssh_host, ssh_port,
                "export PATH=\"$HOME/.local/bin:$PATH\" && "
                "uv pip install -q --python /root/venv312/bin/python "
                "'euroeval[generative]==17.7.0' ninja 2>&1 | tail -40")
        print(r.stdout)

        # No torch/torchvision downgrade here anymore — that was chasing
        # the wrong layer. The real requirement is vllm 0.26.0's own
        # compiled extension needing CUDA 13 runtime libs directly
        # (confirmed via traceback: `import vllm._C_stable_libtorch` ->
        # `ImportError: libcudart.so.13`), independent of whatever torch
        # build accompanies it. Downgrading torch to cu124 fixed torch's
        # own driver check but left vllm's native code still wanting cu13.
        # Fix is upstream of this function: search_offers now requires
        # cuda_vers>=13.0, so euroeval[generative]'s natural (unpinned)
        # dependency resolution should just match the host's actual driver.

        print("\nUploading checkpoints (~3.6GB total) …")
        # Upload ONLY the `final` subdirectory, not its parent — the parent
        # also contains HF Trainer's intermediate checkpoint-NNNN snapshots
        # (each a full ~448MB model copy; gptnl_8b alone had 3.1GB of them).
        # Uploading those blew through the previous attempt's disk/transfer
        # and killed the whole run.
        for cond in CONDITIONS:
            remote_cond_dir = f"{REMOTE_BASE}/checkpoints/checkpoint_{cond}_seed{SEED}"
            ssh(ssh_host, ssh_port, f"mkdir -p {remote_cond_dir} {REMOTE_BASE}/results")
            final_dir = CKPT_DIR / f"checkpoint_{cond}_seed{SEED}" / "final"
            scp_up_dir(ssh_host, ssh_port, final_dir, remote_cond_dir)

        print("\nRunning EuroEval per condition (nohup, background) …")
        # No inner single quotes here — the previous attempt wrapped each
        # segment's `echo '=== {cond} ==='` inside an OUTER single-quoted
        # `bash -c '...'`, which prematurely closed the outer quoting and
        # corrupted the whole command into a syntax error. That error went
        # to nohup.out, not any results/*.log, so the poll loop silently
        # waited the full 6h timeout seeing nothing. The per-condition
        # marker isn't essential — each condition already writes to its own
        # named log file — so it's dropped rather than re-quoted.
        eval_cmds = []
        for cond in CONDITIONS:
            ckpt_path = f"checkpoints/checkpoint_{cond}_seed{SEED}/final"
            eval_cmds.append(
                f"PATH=\"/root/venv312/bin:$PATH\" FULL_LOG=1 /root/venv312/bin/euroeval "
                f"-m {ckpt_path} -l nl --evaluate-test-split --no-progress-bar "
                f"--generative-type base --num-iterations 1 "
                f"> results/{cond}.log 2>&1"
            )
        script = (
            f"cd {REMOTE_BASE} && nohup bash -c \"" + " ; ".join(eval_cmds) +
            " ; touch results/ALL_DONE\" > nohup.out 2>&1 & echo $!"
        )
        r = ssh(ssh_host, ssh_port, script)
        print(f"  Launched, PID: {r.stdout.strip()}")

        # Sanity check shortly after launch — catch an immediate syntax/
        # command-not-found error within ~30s instead of discovering it only
        # after the full poll timeout (this exact failure mode is what ate
        # 6h on the previous attempt).
        time.sleep(30)
        sanity = ssh(ssh_host, ssh_port, f"cat {REMOTE_BASE}/nohup.out 2>&1")
        if sanity.stdout.strip():
            print(f"  nohup.out has early output (check for errors):\n{sanity.stdout}")
        ps_check = ssh(ssh_host, ssh_port, "pgrep -f euroeval")
        if not ps_check.stdout.strip():
            print("  WARNING: no euroeval process found running 30s after launch — "
                  "likely crashed immediately. nohup.out above should show why.")

        print(f"\nPolling every {POLL_INTERVAL}s (max {MAX_WAIT//3600}h) …")
        start = time.time()
        done = False
        while time.time() - start < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            elapsed_m = (time.time() - start) / 60
            check = ssh(ssh_host, ssh_port, f"test -f {REMOTE_BASE}/results/ALL_DONE && echo DONE || echo RUNNING")
            status = check.stdout.strip()
            prog = ssh(ssh_host, ssh_port,
                       f"cd {REMOTE_BASE} && ls -t results/*.log 2>/dev/null | head -1 | "
                       f"xargs -I{{}} sh -c 'echo {{}}: $(tail -c 150 {{}})'")
            print(f"  [{elapsed_m:.1f}m] {status} | {prog.stdout.strip()}", flush=True)
            if status == "DONE":
                done = True
                break

        if not done:
            print("WARNING: timeout — downloading whatever finished.")

        print("\nDownloading results …")
        for cond in CONDITIONS:
            scp_down(ssh_host, ssh_port, f"results/{cond}.log", RESULTS_DIR)

    except Exception as e:
        print(f"\nERROR: {e}")
    finally:
        # A transient local network blip during THIS call once left an
        # instance running undestroyed (destroy_instance itself raised,
        # uncaught, killing the process) — retry with backoff instead of a
        # single unguarded attempt, and if it still fails, print the
        # instance ID as loudly as possible so it isn't silently orphaned.
        print(f"\nDestroying instance #{instance_id} …")
        for attempt in range(1, 6):
            try:
                vast.destroy_instance(id=instance_id)
                print("  Done.")
                break
            except Exception as e:
                print(f"  destroy attempt {attempt}/5 failed: {e}")
                if attempt < 5:
                    time.sleep(15)
        else:
            print(f"\n{'!' * 70}\n"
                  f"WARNING: FAILED TO DESTROY INSTANCE #{instance_id} AFTER 5 ATTEMPTS.\n"
                  f"It is likely still running and billing. Destroy it manually:\n"
                  f"  python -c \"from vastai import VastAI; "
                  f"VastAI(api_key='...').destroy_instance(id={instance_id})\"\n"
                  f"{'!' * 70}")


if __name__ == "__main__":
    main()
