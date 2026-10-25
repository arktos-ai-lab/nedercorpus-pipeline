"""
Download Dutch public-domain books and texts from the Internet Archive.

The Internet Archive (archive.org) hosts millions of digitised public-domain books.
We query for Dutch-language texts with confirmed PD/CC0 licences and download the
plain-text OCR (DjVuTxt format, file suffix _djvu.txt or .txt).

License: Public Domain
Expected size: ~50,000 Dutch PD books

Acquisition strategy:
  1. Internet Archive Advanced Search API → list of Dutch PD item identifiers.
  2. For each item, fetch the item metadata and locate the best text file.
  3. Download text and save to JSONL.

Usage:
  python -m pipeline acquire --sources internet_archive_nl
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "internet_archive_nl"
LICENSE = "PD"
COUNTRY = "NL"
DOMAIN  = "literature"

_IA_SEARCH = "https://archive.org/advancedsearch.php"
_IA_META   = "https://archive.org/metadata/{identifier}"
_IA_DL     = "https://archive.org/download/{identifier}/{filename}"
_UA        = "NederCorpus/0.1 (academic research; PD Dutch texts)"
_DELAY     = 0.5
_MAX_DOCS  = 100_000

# PD licence URL fragments that appear in IA metadata
_PD_MARKERS = (
    "publicdomain", "public-domain", "public_domain",
    "creativecommons.org/licenses/publicdomain",
    "creativecommons.org/publicdomain/zero",
    "creativecommons.org/publicdomain/mark",
    "us-pd", "cc0",
)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _UA}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            if attempt < retries - 1:
                time.sleep(3.0 * (attempt + 1))
        except Exception as exc:
            logger.debug("[ia_nl] %s: %s", url, exc)
            if attempt < retries - 1:
                time.sleep(3.0 * (attempt + 1))
    return None


def _search_page(page: int, rows: int = 500) -> tuple[list[dict], int]:
    """
    Query IA Advanced Search for Dutch PD texts.
    Returns (items, total_results).
    Each item: {identifier, title, year, language, subject}
    """
    params = {
        "q":      "language:(Dutch OR Nederlands) AND mediatype:texts AND licenseurl:(*publicdomain* OR *cc0*)",
        "fl":     "identifier,title,year,language,subject,licenseurl,creator",
        "sort":   "identifier asc",
        "output": "json",
        "rows":   str(rows),
        "page":   str(page),
    }
    url = _IA_SEARCH + "?" + urllib.parse.urlencode(params)
    raw = _fetch(url, timeout=30)
    if not raw:
        return [], 0
    try:
        data = json.loads(raw)
        docs = data.get("response", {}).get("docs", [])
        total = int(data.get("response", {}).get("numFound", 0))
        return docs, total
    except Exception as exc:
        logger.warning("[ia_nl] Search parse error: %s", exc)
        return [], 0


def _get_text_file(identifier: str) -> tuple[str, str]:
    """
    Fetch metadata for an IA item and return (text_content, filename).
    Prefers _djvu.txt, then .txt, then full text search txt.
    IA metadata 'files' can be a list [{name, format, size}] or
    a dict {"/filename": {format, size}}.
    """
    meta_url = _IA_META.format(identifier=identifier)
    raw = _fetch(meta_url, timeout=30)
    if not raw:
        return "", ""
    try:
        meta = json.loads(raw)
    except Exception:
        return "", ""

    raw_files = meta.get("files", [])
    if not raw_files:
        return "", ""

    # Normalise both list and dict formats to {fname: {size, format}}
    files: dict[str, dict] = {}
    if isinstance(raw_files, list):
        for entry in raw_files:
            if isinstance(entry, dict):
                fname = str(entry.get("name") or "").lstrip("/")
                if fname:
                    files[fname] = entry
    elif isinstance(raw_files, dict):
        for fname, fmeta in raw_files.items():
            files[fname.lstrip("/")] = fmeta if isinstance(fmeta, dict) else {}

    if not files:
        return "", ""

    # Priority: DjVu OCR text > plain text
    candidates: list[tuple[int, str]] = []
    for fname, fmeta in files.items():
        fl = fname.lower()
        if fl.endswith("_djvu.txt"):
            candidates.append((0, fname))
        elif fl.endswith("_full.txt"):
            candidates.append((1, fname))
        elif fl.endswith(".txt") and "_meta" not in fl and "_files" not in fl:
            sz = int(fmeta.get("size", 0) or 0)
            if sz > 1000:
                candidates.append((2, fname))

    if not candidates:
        return "", ""

    candidates.sort(key=lambda x: x[0])
    for _, fname in candidates[:3]:
        dl_url = _IA_DL.format(identifier=identifier, filename=fname)
        raw_text = _fetch(dl_url, timeout=120)
        if raw_text and len(raw_text) > 500:
            try:
                text = raw_text.decode("utf-8", errors="replace")
            except Exception:
                continue
            # Strip DjVu header/footer
            text = re.sub(r'\x0c', '\n', text)  # form-feed → newline
            text = re.sub(r'\n{4,}', '\n\n', text)
            return text[:500_000], fname
    return "", ""


def _is_pd(item: dict) -> bool:
    lic = str(item.get("licenseurl", "") or "").lower()
    return any(marker in lic for marker in _PD_MARKERS)


def download(output_dir: Path, max_docs: int = _MAX_DOCS, dry_run: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        logger.info("[ia_nl] [dry-run] would download up to %d Dutch PD texts", max_docs)
        return

    out_path  = output_dir / "ia_nl_records.jsonl"
    seen_file = output_dir / "ia_nl_seen.txt"

    seen: set[str] = set()
    if seen_file.exists():
        seen = {l.strip() for l in seen_file.read_text(encoding="utf-8").splitlines() if l.strip()}

    total_written = sum(1 for _ in out_path.open(encoding="utf-8", errors="replace")) if out_path.exists() else 0
    logger.info("[ia_nl] Resuming — %d seen, %d written", len(seen), total_written)

    rows_per_page = 500
    page = 1

    with (out_path.open("a", encoding="utf-8")) as fout, (seen_file.open("a", encoding="utf-8")) as fseen:
        while total_written < max_docs:
            items, total_results = _search_page(page, rows_per_page)
            if not items:
                logger.info("[ia_nl] No more search results at page %d", page)
                break
            logger.info("[ia_nl] Page %d: %d items (total=%d)", page, len(items), total_results)

            for item in items:
                if total_written >= max_docs:
                    break
                ident = item.get("identifier", "")
                if not ident or ident in seen:
                    continue
                if not _is_pd(item):
                    seen.add(ident)
                    fseen.write(ident + "\n")
                    continue

                text, fname = _get_text_file(ident)
                seen.add(ident)
                fseen.write(ident + "\n")

                if len(text) < 500:
                    time.sleep(_DELAY * 0.3)
                    continue

                # Extract year
                year = None
                yr = item.get("year")
                if yr:
                    try:
                        year = int(str(yr)[:4])
                    except (ValueError, TypeError):
                        pass

                title   = item.get("title", ident)
                if isinstance(title, list):
                    title = title[0] if title else ident
                creator = item.get("creator", "")
                if isinstance(creator, list):
                    creator = "; ".join(creator)

                rec = {
                    "id":               ident,
                    "title":            str(title)[:500],
                    "creator":          str(creator)[:300],
                    "text":             text,
                    "year":             year,
                    "source_url":       f"https://archive.org/details/{ident}",
                    "license":          LICENSE,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
                total_written += 1

                if total_written % 100 == 0:
                    logger.info("[ia_nl] %d items written", total_written)
                time.sleep(_DELAY)

            if len(items) < rows_per_page:
                break
            page += 1
            time.sleep(1.0)

    logger.info("[ia_nl] Done. %d items saved", total_written)
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": SOURCE, "url": "https://archive.org/",
            "license": LICENSE, "acquisition_date": acq_date,
            "total_documents": total_written,
        }, ensure_ascii=False) + "\n")
