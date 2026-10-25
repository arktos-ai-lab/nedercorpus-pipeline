"""
Harvest Flemish legislation from the Vlaamse Codex open data portal.

Vlaamse Codex (codex.vlaanderen.be) publishes the complete consolidated
collection of Flemish legislation (decrees, government decisions, circulars)
from 1951 to present, ~39,000 documents, ~22,000 with full text.

API: OData JSON list   https://codex.opendata.api.vlaanderen.be/api/WetgevingDocument/
Text: HTML per-doc     https://codex.vlaanderen.be/PrintDocument.ashx?id={id}

License: CC-BY (Open Data Vlaanderen free-reuse model)

Usage:
  python -m pipeline acquire --sources vlaamse_codex
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "vlaamse_codex"
LICENSE = "CC-BY"
COUNTRY = "NL-BE"
DOMAIN  = "legal"

_LIST_BASE = "https://codex.opendata.api.vlaanderen.be/api/WetgevingDocument"
_TEXT_BASE = "https://codex.vlaanderen.be/PrintDocument.ashx"
_UA        = "NederCorpus/0.1 (academic; Dutch corpus; research)"
_DELAY     = 0.5
_TIMEOUT   = 60
_PAGE_SIZE = 200

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch_json(url: str, retries: int = 5) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = 10 * (2 ** attempt)
                logger.warning("[vlaamse_codex] HTTP %d — retry in %.0fs", exc.code, wait)
                time.sleep(wait)
            else:
                raise
        except (urllib.error.URLError, OSError) as exc:
            wait = 10 * (2 ** attempt)
            logger.warning("[vlaamse_codex] network error %s — retry in %.0fs", exc, wait)
            time.sleep(wait)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("[vlaamse_codex] parse error: %s", exc)
            return None
    return None


def _fetch_html(doc_id: int, retries: int = 4) -> str | None:
    url = f"{_TEXT_BASE}?id={doc_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NederCorpus/0.1"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = 15 * (2 ** attempt)
                logger.warning("[vlaamse_codex] HTML HTTP %d id=%d — retry in %.0fs",
                               exc.code, doc_id, wait)
                time.sleep(wait)
            else:
                return None
        except (urllib.error.URLError, OSError) as exc:
            wait = 10 * (2 ** attempt)
            logger.warning("[vlaamse_codex] HTML error %s id=%d — retry in %.0fs",
                           exc, doc_id, wait)
            time.sleep(wait)
    return None


def _html_to_text(html: str) -> str:
    body = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL)
    content = body.group(1) if body else html
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def download(output_dir: Path, dry_run: bool = False) -> None:
    if dry_run:
        logger.info("[vlaamse_codex] dry_run — skipping")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file   = output_dir / "records.jsonl"
    state_file = output_dir / "progress.json"

    # Resume: collect already-saved doc_ids and the last list-page skip offset
    seen_ids: set[int] = set()
    if out_file.exists():
        with out_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen_ids.add(int(json.loads(line)["doc_id"]))
                except (KeyError, ValueError, json.JSONDecodeError):
                    pass
        logger.info("[vlaamse_codex] Resuming — %d records already saved", len(seen_ids))

    skip = 0
    if state_file.exists():
        try:
            skip = json.loads(state_file.read_text(encoding="utf-8")).get("skip", 0)
            logger.info("[vlaamse_codex] Resuming list from skip=%d", skip)
        except (json.JSONDecodeError, OSError):
            pass

    written = 0
    skipped_no_text = 0

    with out_file.open("a", encoding="utf-8") as fh:
        while True:
            # Use old API format (taal=nl&skip=N&limit=M) — supports real pagination
            list_url = f"{_LIST_BASE}?taal=nl&skip={skip}&limit={_PAGE_SIZE}"
            data = _fetch_json(list_url)
            if data is None:
                logger.error("[vlaamse_codex] Failed to fetch list at skip=%d — stopping", skip)
                break

            records = data.get("ResultatenLijst", []) if isinstance(data, dict) else data
            total   = data.get("TotaalAantal", "?") if isinstance(data, dict) else "?"
            if not records:
                logger.info("[vlaamse_codex] No more records at skip=%d", skip)
                break

            logger.info("[vlaamse_codex] Page skip=%d / total=%s — %d records in list",
                        skip, total, len(records))

            for rec in records:
                doc_id = rec.get("Id")
                if doc_id is None or not rec.get("HeeftInhoud"):
                    continue
                if doc_id in seen_ids:
                    continue

                html = _fetch_html(doc_id)
                if html is None:
                    skipped_no_text += 1
                    seen_ids.add(doc_id)
                    continue

                text = _html_to_text(html)
                if len(text) < 150:
                    skipped_no_text += 1
                    seen_ids.add(doc_id)
                    continue

                title    = (rec.get("Opschrift") or "").strip()
                date_raw = rec.get("Datum") or ""
                year     = None
                if date_raw:
                    try:
                        year = int(date_raw[:4])
                    except ValueError:
                        pass
                doc_type = rec.get("WetgevingDocumentType") or rec.get("Type") or ""

                entry = {
                    "id":               f"vlaamse_codex_{doc_id}",
                    "source":           SOURCE,
                    "source_url":       f"https://codex.vlaanderen.be/PrintDocument.ashx?id={doc_id}",
                    "license":          LICENSE,
                    "country":          COUNTRY,
                    "domain":           DOMAIN,
                    "language_variant": "NL-BE",
                    "publication_year": year,
                    "acquisition_date": datetime.date.today().isoformat(),
                    "text":             text,
                    "title":            title,
                    "doc_type":         doc_type,
                    "doc_id":           doc_id,
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
                seen_ids.add(doc_id)
                written += 1
                time.sleep(_DELAY)

            skip += len(records)
            state_file.write_text(json.dumps({"skip": skip}), encoding="utf-8")

            if len(records) < _PAGE_SIZE:
                break

    if state_file.exists():
        state_file.unlink()

    logger.info(
        "[vlaamse_codex] Done — %d written, %d skipped (no text)",
        written, skipped_no_text,
    )
