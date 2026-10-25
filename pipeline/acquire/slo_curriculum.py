"""
Download SLO (Stichting Leerplanontwikkeling) curriculum standards data.

SLO is the Dutch national institute for curriculum development.
Their open data portal at opendata.slo.nl publishes all Dutch curriculum
standards (kerndoelen, eindtermen, referentieniveaus) under CC-BY 4.0.

API: https://opendata.slo.nl/curriculum/api/v1/
License: CC-BY 4.0
Domain: education
Country: NL
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "slo_curriculum"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "education"

_API_BASE   = "https://opendata.slo.nl/curriculum/api/v1"
_USER_AGENT = "NederCorpus/0.1 (academic research; open education data)"

# Top-level resource endpoints to harvest
_ENDPOINTS = [
    "kerndoel",
    "eindterm",
    "referentieniveau",
    "leergebied",
    "vak",
    "bouwsteen",
]


def _fetch_json(url: str, retries: int = 3, timeout: int = 30) -> dict | list:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _collect_text(obj: dict | list | str | None, depth: int = 0) -> str:
    """Recursively extract text from nested JSON structures."""
    if obj is None or depth > 5:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, list):
        parts = [_collect_text(item, depth + 1) for item in obj]
        return " ".join(p for p in parts if p)
    if isinstance(obj, dict):
        text_keys = ["omschrijving", "beschrijving", "tekst", "toelichting",
                     "naam", "name", "title", "label", "inhoud", "content"]
        parts = []
        for key in text_keys:
            if key in obj:
                parts.append(_collect_text(obj[key], depth + 1))
        # Also recurse into child items
        for key in ["items", "children", "onderdelen", "bouwstenen"]:
            if key in obj:
                parts.append(_collect_text(obj[key], depth + 1))
        return " ".join(p for p in parts if p)
    return ""


def download(output_dir: Path, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()
    out_file = output_dir / "slo_curriculum.jsonl"

    if out_file.exists() and out_file.stat().st_size > 0:
        logger.info("[slo_curriculum] Already acquired — skipping.")
        return

    if dry_run:
        logger.info("[slo_curriculum] [dry-run] Would query %s", _API_BASE)
        return

    # Verify API is accessible (requires API key from opendata.slo.nl)
    try:
        test_url = f"{_API_BASE}/kerndoel"
        req = urllib.request.Request(test_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "html" in ct.lower():
                logger.warning(
                    "[slo_curriculum] API requires authentication. "
                    "Request a free API key at opendata.slo.nl — skipping."
                )
                return
    except Exception as exc:
        logger.warning("[slo_curriculum] API unreachable: %s — skipping.", exc)
        return

    logger.info("[slo_curriculum] Querying SLO curriculum API: %s", _API_BASE)
    _log(output_dir, _API_BASE)

    total = 0

    with out_file.open("w", encoding="utf-8") as fout:
        for endpoint in _ENDPOINTS:
            url = f"{_API_BASE}/{endpoint}"
            logger.info("[slo_curriculum] Fetching: %s", url)

            try:
                data = _fetch_json(url)
            except Exception as exc:
                logger.warning("[slo_curriculum] %s failed: %s", endpoint, exc)
                time.sleep(1.0)
                continue

            # Normalize to list
            items = data if isinstance(data, list) else data.get("items", data.get("data", [data]))
            if not isinstance(items, list):
                items = [items]

            for item in items:
                if not isinstance(item, dict):
                    continue

                text = _collect_text(item)
                if len(text) < 30:
                    continue

                # Get identifier/title
                item_id = (
                    item.get("uuid") or item.get("id") or
                    item.get("code") or item.get("identifier", "")
                )
                title = (
                    item.get("naam") or item.get("name") or
                    item.get("label") or item.get("titel") or ""
                )
                source_url = item.get("uri") or item.get("url") or url

                record = {
                    "title": str(title),
                    "text": text,
                    "endpoint": endpoint,
                    "item_id": str(item_id),
                    "source_url": str(source_url),
                    "license": LICENSE,
                    "country": COUNTRY,
                    "domain": DOMAIN,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1

            logger.info("[slo_curriculum] %s: %d items", endpoint, len(items))
            time.sleep(0.5)

    logger.info("[slo_curriculum] Done. %d curriculum items saved.", total)
