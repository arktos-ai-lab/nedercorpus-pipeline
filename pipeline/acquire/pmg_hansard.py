"""
South African Parliament Hansard and committee minutes via PMG API.

Parliamentary Monitoring Group (PMG) provides free JSON access to National Assembly
and NCOP Hansard records and committee meeting minutes. Content is bilingual
(Afrikaans and English); we extract Afrikaans-language content and passages.

License: CC-BY 3.0 ZA
API:     https://api.pmg.org.za/
GitHub:  https://github.com/OpenUpSA/pmg-cms-2

Usage:
  python -m pipeline acquire --sources pmg_hansard
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "pmg_hansard"
LICENSE = "CC-BY"
DOMAIN  = "parliamentary"
COUNTRY = "ZA"

_BASE  = "https://api.pmg.org.za"
_UA    = "NederCorpus/0.1 (academic research; contact: corpus@example.org)"
_DELAY = 0.5
_PAGE  = 50
_MAX   = 50_000


def _get(url: str, retries: int = 3) -> dict | list | None:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 410):
                return None
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            logger.warning("[pmg] %s: %s (attempt %d)", url, exc, attempt + 1)
            time.sleep(2.0 * (attempt + 1))
    return None


def _looks_afrikaans(text: str) -> bool:
    """Heuristic: Afrikaans marker words."""
    af_words = {"die", "van", "en", "is", "in", "het", "nie", "op", "te", "wat",
                "dit", "hy", "sy", "ons", "hulle", "aan", "vir", "met", "sal",
                "kan", "word", "ook", "maar", "as", "by", "na", "so", "hom"}
    words = text.lower().split()[:60]
    if not words:
        return False
    hits = sum(1 for w in words if w in af_words)
    return hits / len(words) >= 0.15


def _extract_text(item: dict) -> tuple[str, str]:
    """Extract (text, title) from a PMG API record."""
    title = item.get("title") or item.get("meeting_name") or ""
    parts = []
    for field in ("body", "content", "summary", "introduction"):
        val = item.get(field)
        if val and isinstance(val, str) and len(val.strip()) > 50:
            parts.append(val.strip())
    # Committee meetings have nested 'content' list
    if not parts:
        for sub in item.get("content", []):
            if isinstance(sub, dict):
                for f in ("body", "content", "text"):
                    v = sub.get(f)
                    if v and isinstance(v, str) and len(v.strip()) > 50:
                        parts.append(v.strip())
    return "\n\n".join(parts), str(title)


def _fetch_endpoint(endpoint: str, output_dir: Path, out_f, saved: list[int], max_docs: int) -> int:
    """Paginate through one PMG endpoint, write Afrikaans docs."""
    page = 1
    count = 0
    acq_date = datetime.date.today().isoformat()
    while saved[0] < max_docs:
        url = f"{_BASE}/{endpoint}?page={page}&per_page={_PAGE}"
        data = _get(url)
        if data is None:
            break
        items = data if isinstance(data, list) else data.get("results", data.get("objects", []))
        if not items:
            break
        for item in items:
            if saved[0] >= max_docs:
                break
            text, title = _extract_text(item)
            if len(text) < 200:
                continue
            # Save all records; langfilter handles language detection downstream
            doc = {
                "id":               str(item.get("id", "")),
                "title":            title,
                "text":             text,
                "date":             str(item.get("date") or item.get("start_date") or ""),
                "url":              item.get("url") or f"{_BASE}/{endpoint}/{item.get('id', '')}",
                "acquisition_date": acq_date,
            }
            out_f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            saved[0] += 1
            count += 1
        # check for next page
        if isinstance(data, dict):
            next_url = data.get("next")
            if not next_url:
                break
        page += 1
        time.sleep(_DELAY)
    return count


def download(output_dir: Path, max_docs: int = _MAX, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[pmg] [dry-run] would download up to %d PMG records", max_docs)
        return

    out_path = output_dir / "pmg_records.jsonl"
    saved = [0]
    if out_path.exists():
        saved[0] = sum(1 for _ in out_path.open(encoding="utf-8", errors="replace"))
        logger.info("[pmg] Resuming — %d records already saved", saved[0])

    endpoints = ["hansard", "committee-meeting"]
    with out_path.open("a", encoding="utf-8") as f:
        for ep in endpoints:
            if saved[0] >= max_docs:
                break
            n = _fetch_endpoint(ep, output_dir, f, saved, max_docs)
            logger.info("[pmg] %s: %d Afrikaans records saved", ep, n)

    logger.info("[pmg] Done. %d total records → %s", saved[0], out_path)

    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": _BASE, "license": LICENSE,
            "acquisition_date": acq_date, "total_documents": saved[0],
        }, ensure_ascii=False) + "\n")
