"""
Download MultiEURLEX EU legal documents in Dutch (nl).

MultiEURLEX is a multilingual dataset of EU legislative documents from EUR-Lex,
annotated with EUROVOC concept labels. CC-BY-4.0.

Source:   HuggingFace coastalcph/multi_eurlex (data/multi_eurlex.tar.gz)
License:  CC-BY-4.0
Domain:   legal
URL:      https://huggingface.co/datasets/coastalcph/multi_eurlex

The tar.gz contains flat JSONL files (dev.jsonl, test.jsonl, train.jsonl).
Each line is a JSON record with 23 EU language versions in "text" dict.
We stream through each file and extract only the "nl" text field.

Usage:
  python -m pipeline acquire --sources multi_eurlex
"""
from __future__ import annotations

import datetime
import json
import logging
import tarfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "multi_eurlex"
LICENSE = "CC-BY"
DOMAIN  = "legal"
COUNTRY = "EU"

_UA = "NederCorpus/0.1 (academic; multi_eurlex nl corpus; contact: corpus@example.org)"

_TAR_URL = (
    "https://huggingface.co/datasets/coastalcph/multi_eurlex"
    "/resolve/main/data/multi_eurlex.tar.gz"
)


def download(
    output_dir: Path,
    dry_run: bool = False,
) -> None:
    """
    Stream the MultiEURLEX tar.gz, extracting Dutch (nl) text from each JSONL line.

    The tar.gz contains dev.jsonl, test.jsonl, train.jsonl — each line is a
    multilingual JSON record. We extract only the "nl" text field.

    Saves: multi_eurlex_nl.jsonl  (one record per line: celex_id + text + labels)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    out_path = output_dir / "multi_eurlex_nl.jsonl"

    # Check if already done
    if out_path.exists() and out_path.stat().st_size > 1_000_000:
        logger.info("[multi_eurlex] %s already exists (%.1f MB) — skipping",
                    out_path.name, out_path.stat().st_size / 1e6)
        _write_log(output_dir, acq_date, ["nl"])
        return

    if dry_run:
        logger.info("[multi_eurlex] [dry-run] would stream %s", _TAR_URL)
        return

    logger.info("[multi_eurlex] Streaming tar.gz from HuggingFace (~2.8 GB compressed)…")
    logger.info("[multi_eurlex] Extracting nl text from each JSONL line on the fly")

    total_written = 0
    downloaded: list[str] = []

    try:
        import requests as _requests

        resp = _requests.get(_TAR_URL, stream=True, timeout=600,
                             headers={"User-Agent": _UA})
        resp.raise_for_status()

        _bytes = [0]
        _last_mb = [0]

        class _Stream:
            def __init__(self, raw):
                self._raw = raw

            def read(self, n):
                data = self._raw.read(n)
                if data:
                    _bytes[0] += len(data)
                    mb = _bytes[0] // (100 * 1024 * 1024)
                    if mb > _last_mb[0]:
                        _last_mb[0] = mb
                        logger.info(
                            "[multi_eurlex] %.0f MB downloaded, %d nl docs written",
                            _bytes[0] / 1e6, total_written,
                        )
                return data

        with out_path.open("wb") as fout:
            with tarfile.open(fileobj=_Stream(resp.raw), mode="r|gz") as tar:
                for member in tar:
                    if not member.isfile():
                        tar.members = []
                        continue

                    split = member.name.split("/")[-1]
                    split = split.replace(".jsonl", "").replace(".json", "")
                    logger.info("[multi_eurlex] Processing %s (%.0f MB uncompressed)…",
                                member.name, member.size / 1e6)

                    f = tar.extractfile(member)
                    if f is None:
                        tar.members = []
                        continue

                    for raw_line in f:
                        try:
                            rec = json.loads(raw_line)
                        except (json.JSONDecodeError, ValueError):
                            continue

                        text_field = rec.get("text") or {}
                        if isinstance(text_field, dict):
                            nl_text = (text_field.get("nl") or "").strip()
                        elif isinstance(text_field, str):
                            nl_text = text_field.strip()
                        else:
                            nl_text = ""

                        if not nl_text:
                            continue

                        out_rec = {
                            "celex_id": rec.get("celex_id") or rec.get("id") or "",
                            "split":    split,
                            "text":     nl_text,
                            "labels":   rec.get("labels") or [],
                        }
                        fout.write((json.dumps(out_rec, ensure_ascii=False) + "\n").encode("utf-8"))
                        total_written += 1

                    logger.info("[multi_eurlex] %s done: %d nl docs total", member.name, total_written)
                    tar.members = []

        resp.close()
        downloaded = ["nl"]
        logger.info("[multi_eurlex] Done. %d Dutch documents written to %s",
                    total_written, out_path.name)

    except Exception as exc:
        logger.error("[multi_eurlex] Streaming failed: %s", exc)
        if not downloaded:
            return

    _write_log(output_dir, acq_date, downloaded)


def _write_log(output_dir: Path, acq_date: str, downloaded: list[str]) -> None:
    log_entry = {
        "source":           SOURCE,
        "license":          LICENSE,
        "acquisition_date": acq_date,
        "languages":        downloaded,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download MultiEURLEX EU legal texts (nl) from HuggingFace (CC-BY-4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
