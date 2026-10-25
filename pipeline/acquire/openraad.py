"""
Download Dutch municipal council documents via OpenRaadsinformatie API.

OpenRaadsinformatie (ORI) provides open access to Dutch municipal council
documents: agendas, motions, decisions, meeting reports.
Over 100 Dutch municipalities participate.

API: https://api.openraadsinformatie.nl/v1/
License: CC0 / CC-BY (per municipality — all participating municipalities
         have agreed to open publication)
Domain: government / municipal
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "openraad"
LICENSE = "CC0"
DOMAIN  = "government"

ORI_API  = "https://api.openraadsinformatie.nl/v1"
_PAGE    = 100
_SHARD   = 1_000   # documents per output file
_HEADERS = {"User-Agent": "NederCorpus/0.1 (academic research; OpenRaadsinformatie CC0)"}


def _log(output_dir: Path, total: int) -> None:
    entry = {
        "source": SOURCE,
        "api": ORI_API,
        "license": LICENSE,
        "total_downloaded": total,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _api_get(path: str, params: dict | None = None) -> dict:
    url = f"{ORI_API}{path}"
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _fetch_documents(output_dir: Path, max_docs: int) -> int:
    """
    Page through ORI search results and save documents as JSONL shards.

    Each saved item has: id, title, text, municipality, date, source_url.
    """
    shard_num   = 0
    total       = 0
    page        = 0
    buffer: list[dict] = []

    def _flush() -> None:
        nonlocal shard_num
        if not buffer:
            return
        path = output_dir / f"openraad_{shard_num:06d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for item in buffer:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        logger.info("[openraad] Shard %d: %d docs", shard_num, len(buffer))
        shard_num += 1
        buffer.clear()

    while total < max_docs:
        try:
            data = _api_get("/search", {
                "q":      "gemeenteraad",   # general council keyword to get all doc types
                "size":   _PAGE,
                "from":   page * _PAGE,
                "nl":     "true",           # Dutch language
            })
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Try alternative endpoint structure
                try:
                    data = _api_get("/items", {"size": _PAGE, "from": page * _PAGE})
                except Exception as exc2:
                    logger.error("[openraad] API error: %s", exc2)
                    break
            else:
                logger.error("[openraad] HTTP %s: %s", exc.code, exc)
                break
        except Exception as exc:
            logger.error("[openraad] Request failed: %s", exc)
            break

        hits = data.get("hits", {})
        items = hits.get("hits", data.get("results", data.get("items", [])))
        if not items:
            break

        for item in items:
            src  = item.get("_source", item)
            text = (
                src.get("description") or
                src.get("text") or
                src.get("body") or
                src.get("content") or ""
            ).strip()
            if len(text) < 100:
                continue
            record = {
                "id":           src.get("id") or src.get("identifier", ""),
                "title":        src.get("name") or src.get("title", ""),
                "text":         text,
                "municipality": src.get("organization", {}).get("name", "") if isinstance(src.get("organization"), dict) else src.get("organization", ""),
                "date":         src.get("start_date") or src.get("date", ""),
                "source_url":   src.get("ori_url") or src.get("source_url") or src.get("url", ""),
            }
            buffer.append(record)
            total += 1
            if len(buffer) >= _SHARD:
                _flush()
            if total >= max_docs:
                break

        page += 1
        time.sleep(0.2)   # be polite

    _flush()
    return total


def download(
    output_dir: Path,
    max_docs: int = 500_000,
    dry_run: bool = False,
) -> None:
    """Download Dutch municipal council documents from OpenRaadsinformatie."""
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[openraad] License: CC0 (OpenRaadsinformatie)")

    if dry_run:
        logger.info("[openraad] [dry-run] skipping")
        return

    existing = list(output_dir.glob("openraad_*.jsonl"))
    if existing:
        logger.info("[openraad] Already downloaded (%d shards) — skipping", len(existing))
        return

    logger.info("[openraad] Fetching up to %d documents …", max_docs)
    total = _fetch_documents(output_dir, max_docs)
    if total:
        _log(output_dir, total)
        logger.info("[openraad] Done: %d documents saved", total)
    else:
        logger.warning(
            "[openraad] No documents fetched. The ORI API endpoint may have changed.\n"
            "  Check https://openraadsinformatie.nl for the current API URL."
        )
