"""
Download EUPDCorp — European Parliament Plenary Debate speeches 1999–2024.

Zenodo record: https://zenodo.org/records/15056399
License: CC BY 4.0
Size: ~563,696 speeches across 5 parliamentary terms (1999–2024) in original
      EU languages.  Dutch (nl) is one of the 24 official languages.
Format: Zenodo download (structured JSON/CSV per term or per language).

Strategy:
  1. Query Zenodo API to get file list for record 15056399
  2. Download only the Dutch (nl) language file(s)
  3. Save as eupdcorp_nl_speeches.jsonl (one speech per line)

Usage:
  python -m pipeline acquire --sources eupdcorp
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "eupdcorp"
LICENSE = "CC-BY"
COUNTRY = "EU"
DOMAIN  = "parliamentary"

_ZENODO_RECORD   = "15056399"
_ZENODO_API      = f"https://zenodo.org/api/records/{_ZENODO_RECORD}"
_ZENODO_FILES    = f"https://zenodo.org/api/records/{_ZENODO_RECORD}/files"
_USER_AGENT      = "NederCorpus/0.1 (academic research; Dutch parliamentary corpus)"
_MAX_RETRIES     = 4


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 120) -> bytes:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = 15.0 * (attempt + 1)
            logger.warning("[eupdcorp] Retry %d for %s: %s (wait %.0fs)",
                           attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _get_file_list() -> list[dict]:
    """Return list of file dicts from Zenodo.  Each has 'key' and download link."""
    # Try the dedicated /files endpoint first (InvenioRDM v3+)
    try:
        data = json.loads(_fetch(_ZENODO_FILES))
        entries = data.get("entries", [])
        if entries:
            return entries
    except Exception as exc:
        logger.warning("[eupdcorp] /files endpoint failed: %s — trying record metadata", exc)

    # Fallback: parse 'files' key from record metadata
    data = json.loads(_fetch(_ZENODO_API))
    files = data.get("files", [])
    if files:
        return files
    # InvenioRDM v3 stores files under 'metadata' → 'related_identifiers' won't help;
    # look under 'links' for HTML page as last resort.
    raise RuntimeError(
        f"[eupdcorp] Cannot determine file list for Zenodo record {_ZENODO_RECORD}. "
        "Check https://zenodo.org/records/15056399 manually."
    )


def _download_url(entry: dict) -> str:
    """Extract download URL from a Zenodo file entry dict."""
    # InvenioRDM v3 uses entry["links"]["content"]
    links = entry.get("links", {})
    if "content" in links:
        return links["content"]
    # Legacy: entry["links"]["self"]
    if "self" in links:
        return links["self"]
    raise KeyError(f"No download link in file entry: {entry}")


def _is_dutch_file(key: str) -> bool:
    """Return True if the filename plausibly contains Dutch-language speeches."""
    key_l = key.lower()
    # Common naming patterns: speeches_nl.*, nl_speeches.*, term*_nl.*, *_dutch.*
    dutch_markers = ["_nl.", "_nl_", "nl_", "dutch", "nld", "netherlands"]
    return any(m in key_l for m in dutch_markers)


def _parse_rds(raw_bytes: bytes, filename: str, out_file: Path) -> int:
    """Parse an R RDS file and extract Dutch speech records using the rdata package."""
    try:
        import rdata
        import io
    except ImportError:
        logger.warning("[eupdcorp] rdata package not installed — cannot parse %s. Run: pip install rdata", filename)
        return 0

    try:
        parsed = rdata.read_rds(io.BytesIO(raw_bytes))
    except Exception as exc:
        logger.warning("[eupdcorp] rdata failed to parse %s: %s", filename, exc)
        return 0

    # RDS may be a pandas DataFrame, dict, or R list — normalise to list of dicts
    rows = []
    try:
        import pandas as pd
        if isinstance(parsed, pd.DataFrame):
            rows = parsed.to_dict(orient="records")
        elif hasattr(parsed, "items"):
            # R list/dict — try to find a speech-like sub-object
            for v in parsed.values():
                if isinstance(v, pd.DataFrame):
                    rows = v.to_dict(orient="records")
                    break
    except ImportError:
        pass

    if not rows:
        logger.warning("[eupdcorp] RDS parsed but no DataFrame found in %s; type: %s", filename, type(parsed))
        return 0

    logger.info("[eupdcorp] RDS %s: %d total rows", filename, len(rows))
    dutch_lang_vals = {"nl", "nld", "dutch", "dut", "netherlands"}
    written = 0
    with out_file.open("a", encoding="utf-8") as fh:
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            # Find text field
            speech = str(rec.get("text") or rec.get("speech") or rec.get("content") or
                        rec.get("speechText") or rec.get("speech_text") or "").strip()
            if not speech or len(speech) < 50:
                continue
            # Language filter
            lang = str(rec.get("language") or rec.get("lang") or rec.get("language_iso") or "nl").lower()
            if lang not in dutch_lang_vals:
                continue
            out_rec = {
                "speech_id": str(rec.get("id") or rec.get("speech_id") or rec.get("speechId") or ""),
                "speaker": str(rec.get("speaker") or rec.get("mep_name") or rec.get("speakerName") or ""),
                "date": str(rec.get("date") or rec.get("sitting_date") or rec.get("agendaItemTitle") or ""),
                "term": str(rec.get("term") or rec.get("parliamentary_term") or rec.get("parliamentaryTerm") or ""),
                "text": speech,
                "source_file": filename,
            }
            fh.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
            written += 1
    return written


def _parse_and_save(raw_bytes: bytes, filename: str, out_file: Path) -> int:
    """
    Detect file format (RDS, JSON array, JSONL, CSV) and extract Dutch speech records.
    Saves one JSONL record per speech to out_file.  Returns count written.
    """
    # Handle R binary format
    if filename.lower().endswith(".rds"):
        return _parse_rds(raw_bytes, filename, out_file)

    text = raw_bytes.decode("utf-8", errors="replace")
    written = 0

    # Try JSON array first
    try:
        records = json.loads(text)
        if isinstance(records, list):
            with out_file.open("a", encoding="utf-8") as fh:
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    speech = rec.get("text") or rec.get("speech") or rec.get("content") or ""
                    if not speech:
                        continue
                    lang = (rec.get("language") or rec.get("lang") or "nl").lower()
                    if lang not in ("nl", "nld", "dutch", "dut"):
                        continue
                    out_rec = {
                        "speech_id": rec.get("id") or rec.get("speech_id") or "",
                        "speaker": rec.get("speaker") or rec.get("mep_name") or "",
                        "date": rec.get("date") or rec.get("sitting_date") or "",
                        "term": rec.get("term") or rec.get("parliamentary_term") or "",
                        "text": speech,
                        "source_file": filename,
                    }
                    fh.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
                    written += 1
            return written
    except json.JSONDecodeError:
        pass

    # Try JSONL
    with out_file.open("a", encoding="utf-8") as fh:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            speech = rec.get("text") or rec.get("speech") or rec.get("content") or ""
            if not speech:
                continue
            lang = (rec.get("language") or rec.get("lang") or "nl").lower()
            if lang not in ("nl", "nld", "dutch", "dut"):
                continue
            out_rec = {
                "speech_id": rec.get("id") or rec.get("speech_id") or "",
                "speaker": rec.get("speaker") or rec.get("mep_name") or "",
                "date": rec.get("date") or rec.get("sitting_date") or "",
                "term": rec.get("term") or rec.get("parliamentary_term") or "",
                "text": speech,
                "source_file": filename,
            }
            fh.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
            written += 1

    if written == 0:
        logger.warning(
            "[eupdcorp] No Dutch speeches extracted from %s — format may differ. "
            "Inspect the file and update _parse_and_save() if needed.",
            filename,
        )
    return written


def download(output_dir: Path, **_kwargs) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "eupdcorp_nl_speeches.jsonl"

    logger.info("[eupdcorp] Fetching Zenodo file list for record %s", _ZENODO_RECORD)
    try:
        files = _get_file_list()
    except Exception as exc:
        logger.error("[eupdcorp] Cannot get file list: %s", exc)
        return

    logger.info("[eupdcorp] %d files in record", len(files))
    for f in files:
        logger.info("  %s", f.get("key", "?"))

    # Filter to Dutch files; if none found by heuristic, use all non-PDF data files
    dutch_files = [f for f in files if _is_dutch_file(f.get("key", ""))]
    if not dutch_files:
        logger.warning(
            "[eupdcorp] No Dutch-specific files detected by heuristic — "
            "will download all data files (including RDS) and filter by language field."
        )
        dutch_files = [f for f in files if not f.get("key", "").lower().endswith(".pdf")]

    total_written = 0
    for entry in dutch_files:
        key = entry.get("key", "unknown")
        size_mb = entry.get("size", 0) / 1e6
        logger.info("[eupdcorp] Downloading %s (%.1f MB)", key, size_mb)
        try:
            url = _download_url(entry)
        except KeyError as exc:
            logger.error("[eupdcorp] %s — skipping %s", exc, key)
            continue

        try:
            raw = _fetch(url, timeout=600)
        except Exception as exc:
            logger.error("[eupdcorp] Download failed for %s: %s", key, exc)
            continue

        _log(output_dir, url)
        n = _parse_and_save(raw, key, out_file)
        logger.info("[eupdcorp] %s → %d Dutch speeches written", key, n)
        total_written += n

    logger.info("[eupdcorp] Total Dutch speeches saved: %d", total_written)
