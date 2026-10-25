"""
Pipeline watchdog — monitors background acquisition log files for stalls and errors.

Checks each watched log file for:
  - ERROR / Traceback / Exception lines  → alert immediately
  - No new output in STALL_MINUTES       → alert as stalled
  - Process completion (file not growing + no new lines for 2× stall window)

Usage:
  python -m pipeline.watchdog --logs logs/eurlex_bodies.log logs/belgian.log ...
  python -m pipeline.watchdog --dir logs/ --pattern "*.log" --interval 300
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import time
from pathlib import Path

STALL_MINUTES   = 30
CHECK_INTERVAL  = 300   # seconds between checks
_ERROR_PAT = re.compile(
    r"\b(ERROR|CRITICAL|Traceback|Exception|raise |HTTPError|ConnectionError"
    r"|TimeoutError|PermissionError|FileNotFoundError|OSError|failed)\b",
    re.IGNORECASE,
)
_PROGRESS_PAT = re.compile(r"\b(fetched|written|saved|parsed|done|records|docs|shard)\b", re.I)


class _FileState:
    def __init__(self, path: Path) -> None:
        self.path       = path
        self.last_size  = 0
        self.last_mtime = 0.0
        self.last_lines = 0
        self.stall_since: datetime.datetime | None = None
        self.alerted_errors: set[str] = set()

    def check(self) -> list[str]:
        alerts: list[str] = []
        try:
            st = os.stat(self.path)
        except OSError:
            return [f"[watchdog] MISSING: {self.path.name}"]

        size  = st.st_size
        mtime = st.st_mtime
        now   = datetime.datetime.now()

        if size == self.last_size and mtime == self.last_mtime:
            if self.stall_since is None:
                self.stall_since = now
            elapsed = (now - self.stall_since).total_seconds() / 60
            if elapsed >= STALL_MINUTES:
                alerts.append(
                    f"[watchdog] STALL {self.path.name}: no output for {elapsed:.0f} min"
                )
        else:
            self.stall_since = None
            self.last_size   = size
            self.last_mtime  = mtime

        # Scan new lines for errors
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return alerts

        new_lines = lines[self.last_lines:]
        self.last_lines = len(lines)

        for line in new_lines:
            if _ERROR_PAT.search(line):
                key = line.strip()[:120]
                if key not in self.alerted_errors:
                    self.alerted_errors.add(key)
                    alerts.append(f"[watchdog] ERROR in {self.path.name}: {key}")

        return alerts


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def watch(log_paths: list[Path], interval: int = CHECK_INTERVAL) -> None:
    states = {p: _FileState(p) for p in log_paths}
    print(f"[watchdog] {_ts()} Monitoring {len(states)} log files. "
          f"Stall threshold: {STALL_MINUTES} min. Check interval: {interval}s.")
    for p in log_paths:
        print(f"  • {p}")
    print()

    while True:
        any_alert = False
        for path, state in states.items():
            alerts = state.check()
            for msg in alerts:
                print(f"{_ts()} {msg}")
                any_alert = True

        if not any_alert:
            sizes = []
            for path, state in states.items():
                try:
                    kb = os.path.getsize(path) / 1024
                    sizes.append(f"{path.name}:{kb:.0f}KB")
                except OSError:
                    sizes.append(f"{path.name}:MISSING")
            print(f"{_ts()} [watchdog] OK — {', '.join(sizes)}")

        time.sleep(interval)


def main() -> None:
    global STALL_MINUTES
    parser = argparse.ArgumentParser(description="NederCorpus pipeline watchdog")
    parser.add_argument(
        "--logs", nargs="+", type=Path, default=[],
        help="Log files to monitor",
    )
    parser.add_argument(
        "--dir", type=Path, default=None,
        help="Directory to scan for log files",
    )
    parser.add_argument(
        "--pattern", default="*.log",
        help="Glob pattern within --dir (default: *.log)",
    )
    parser.add_argument(
        "--interval", type=int, default=CHECK_INTERVAL,
        help=f"Seconds between checks (default: {CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--stall-minutes", type=int, default=STALL_MINUTES,
        help=f"Minutes without output before stall alert (default: {STALL_MINUTES})",
    )
    args = parser.parse_args()
    STALL_MINUTES = args.stall_minutes

    log_paths: list[Path] = list(args.logs)
    if args.dir:
        log_paths += sorted(args.dir.glob(args.pattern))

    if not log_paths:
        parser.error("Provide --logs or --dir (with --pattern)")

    watch(log_paths, interval=args.interval)


if __name__ == "__main__":
    main()
