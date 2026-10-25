"""
Download BHIC + regional Dutch archive HTR transcriptions from Zenodo.

Zenodo record 11209325 (IJsberg HTR corpus, CC-BY 4.0) contains machine-
transcribed text from 12+ Dutch regional archives:
  BHIC  — Brabants Historisch Informatie Centrum (notarial 1642–1935)
  NHA   — Noord-Hollands Archief (notarial)
  GRA   — Groninger Archieven
  ZA    — Zeeuws Archief
  RHCL  — Regionaal Historisch Centrum Limburg
  GA    — Gelders Archief
  HCO   — Historisch Centrum Overijssel
  HUA   — Het Utrechts Archief (34-1)
  Tresoar — Frisian archive (Leeuwarden)
  DA    — Dutch Caribbean / WIC records
  1.05.xx — Nationaal Archief Hoge Raad / States-General collections

VOC records (1.04.02) are excluded — covered by globalise_voc.

Strategy:
  1. Fetch file listing from Zenodo API
  2. Download only TXT ZIP files (skip VOC, skip PAGE XML, skip images)
  3. Extract per-page TXT files, batch 100 pages per JSONL record
  4. Save to bhic_htr_{archive_code}.jsonl

License: CC-BY 4.0  (DOI: 10.5281/zenodo.11209325)
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import re
import time
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "bhic_htr"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "historical"

_ZENODO_API  = "https://zenodo.org/api/records/11209325"
_ZENODO_BASE = "https://zenodo.org/records/11209325/files"
_UA          = "NederCorpus/0.1 (academic; Dutch HTR corpus; CC-BY 4.0 open data)"

# Exclude VOC (covered by globalise_voc), page XMLs, images, spreadsheets
_SKIP_PATTERNS = (
    "VOC TXT",
    "VOC PAGE",
    "1.04.02",   # VOC Testamenten
    "PAGE.zip",
    ".7z",
    ".xlsx",
    "Images",
    "Ground Truth",
    "trainingset",
    "Indices",
)

# Pages to group per JSONL document (avoid tiny/huge docs)
_PAGES_PER_DOC = 50


def _fetch_file_list() -> list[dict]:
    """Fetch Zenodo file listing via API."""
    req = urllib.request.Request(
        _ZENODO_API,
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            return data.get("files", [])
        except Exception as exc:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return []


def _should_skip(filename: str) -> bool:
    return any(p in filename for p in _SKIP_PATTERNS)


def _archive_code(filename: str) -> str:
    """Derive a short archive identifier from the filename."""
    # e.g. "HTR results BHIC 7048 TXT.zip" → "BHIC_7048"
    # "HTR results NHA Notarial 1617 TXT.zip" → "NHA_1617"
    # "HTR results 1.05.03 TXT.zip" → "1_05_03"
    # "HTR results Tresoar 26 TXT.zip" → "Tresoar_26"
    name = filename.replace("HTR results ", "").replace(" TXT.zip", "").replace("HTR_results_", "")
    name = re.sub(r"\s+", "_", name.strip())
    name = re.sub(r"[^\w\-]", "_", name)
    return name


def _download_bytes(url: str, retries: int = 4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 10 * (attempt + 1)
            logger.warning("[bhic_htr] Retry %d for %s: %s (wait %ds)", attempt + 1, url[:60], exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _extract_txt_from_zip(zip_bytes: bytes) -> list[str]:
    """
    Extract text content from all .txt files within a ZIP.
    Returns list of non-empty page texts.
    """
    pages = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            txt_names = sorted(
                n for n in zf.namelist()
                if n.lower().endswith(".txt") and not n.startswith("__MACOSX")
            )
            for name in txt_names:
                try:
                    raw = zf.read(name)
                    # Try UTF-8 then latin-1 (historical docs often latin-1)
                    for enc in ("utf-8", "latin-1"):
                        try:
                            text = raw.decode(enc).strip()
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        continue
                    if len(text) >= 20:
                        pages.append(text)
                except Exception:
                    continue
    except zipfile.BadZipFile:
        logger.warning("[bhic_htr] BadZipFile — skipping")
    return pages


def download(
    output_dir: Path,
    max_zips: int = 100,
    dry_run: bool = False,
) -> None:
    """
    Download BHIC + Dutch regional archive HTR TXT zips from Zenodo.

    Each archive collection is saved as bhic_htr_{archive_code}.jsonl.
    Each JSONL record groups _PAGES_PER_DOC sequential page transcriptions.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[bhic_htr] [dry-run] would fetch Zenodo record %s", _ZENODO_API)
        return

    logger.info("[bhic_htr] Fetching Zenodo file listing…")
    files = _fetch_file_list()

    # Filter to downloadable TXT zips
    todo = [
        f for f in files
        if f["key"].endswith("TXT.zip") and not _should_skip(f["key"])
    ]
    logger.info("[bhic_htr] %d TXT zips to download (of %d total files)", len(todo), len(files))

    acq_date   = datetime.date.today().isoformat()
    total_docs = 0

    for idx, file_info in enumerate(todo[:max_zips]):
        filename = file_info["key"]
        size_mb  = file_info["size"] // 1024 // 1024
        code     = _archive_code(filename)
        out_path = output_dir / f"bhic_htr_{code}.jsonl"

        if out_path.exists():
            n = sum(1 for _ in out_path.open(encoding="utf-8"))
            logger.info("[bhic_htr] %s already done (%d docs), skipping", code, n)
            total_docs += n
            continue

        url = f"{_ZENODO_BASE}/{urllib.request.quote(filename)}?download=1"
        logger.info("[bhic_htr] [%d/%d] %s (%d MB) → %s",
                    idx + 1, len(todo), filename, size_mb, out_path.name)

        try:
            zip_bytes = _download_bytes(url)
        except Exception as exc:
            logger.warning("[bhic_htr] Download failed for %s: %s", filename, exc)
            continue

        pages = _extract_txt_from_zip(zip_bytes)
        if not pages:
            logger.info("[bhic_htr] No text pages in %s", filename)
            continue

        logger.info("[bhic_htr] %s: %d pages extracted", code, len(pages))

        # Batch pages into documents
        doc_count = 0
        with out_path.open("w", encoding="utf-8") as fout:
            for batch_start in range(0, len(pages), _PAGES_PER_DOC):
                batch    = pages[batch_start: batch_start + _PAGES_PER_DOC]
                combined = "\n\n".join(batch)
                if len(combined) < 100:
                    continue
                rec = {
                    "id":               f"{code}_{batch_start:07d}",
                    "archive":          code,
                    "source_file":      filename,
                    "pages_start":      batch_start,
                    "pages_end":        batch_start + len(batch),
                    "text":             combined,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                doc_count += 1

        total_docs += doc_count
        logger.info("[bhic_htr] %s: %d documents written", code, doc_count)

        time.sleep(2.0)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source":           SOURCE,
            "url":              _ZENODO_API,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_docs":       total_docs,
        }, ensure_ascii=False) + "\n")

    logger.info("[bhic_htr] Done. %d documents total.", total_docs)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Dutch regional archive HTR transcriptions (Zenodo 11209325, CC-BY 4.0)"
    )
    parser.add_argument("--output",    required=True, type=Path)
    parser.add_argument("--max-zips",  type=int, default=100)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()
    download(args.output, max_zips=args.max_zips, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
