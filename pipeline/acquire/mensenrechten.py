"""
Download equality body decisions (oordelen) from College voor de Rechten van de Mens.

The College voor de Rechten van de Mens (formerly Commissie Gelijke Behandeling)
is the Dutch national human rights and equality body. Its oordelen are authoritative
decisions on discrimination complaints. They are published at oordelen.mensenrechten.nl
and are reusable with attribution (attribution-permissive custom license).

~5,000 oordelen spanning 1999-present, full text in HTML, server-rendered.

URL pattern: https://oordelen.mensenrechten.nl/oordeel/{year}-{number:04d}
Numbers are tried sequentially per year; consecutive 404/400 stops iteration.

Usage:
  python -m pipeline acquire --sources mensenrechten
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "mensenrechten"
LICENSE = "CC-BY"   # attribution-permissive custom license; treated as CC-BY compatible
DOMAIN  = "legal"
COUNTRY = "NL"

_BASE       = "https://oordelen.mensenrechten.nl"
_UA         = "NederCorpus/0.1 (academic research; Dutch corpus; contact: corpus@example.org)"
_DELAY      = 0.5   # seconds between requests
_MAX_MISS   = 15    # stop year when this many consecutive misses seen
_START_YEAR = 1999
_MAX_PER_YEAR = 300  # generous upper bound per year

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch(url: str) -> str | None:
    """Fetch URL, return HTML string. None on 404/400/error."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404):
            return None
        logger.warning("[mensenrechten] HTTP %d for %s", exc.code, url)
        time.sleep(2.0)
        return None
    except Exception as exc:
        logger.warning("[mensenrechten] Error for %s: %s", url, exc)
        return None


def _parse_oordeel(html: str, url: str, year: int, number: int) -> dict | None:
    """Extract structured data from an oordeel HTML page."""
    # Verify it's a real oordeel page (not a redirect to homepage)
    if "oordeel-tekst" not in html and "oordeel-samenvatting" not in html:
        return None

    # Title from h1 inside article.content
    title_m = re.search(r'class="article content">.*?<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    title = re.sub(r'\s+', ' ', title_m.group(1)).strip() if title_m else ""

    # Metadata fields: Datum, Discriminatiegrond, Terrein, Trefwoord
    date_m = re.search(r'<span>Datum:</span>\s*<span>([^<]+)</span>', html)
    date = date_m.group(1).strip() if date_m else ""

    grond_m = re.search(r'<span>Discriminatiegrond:</span>\s*<span>([^<]+)</span>', html)
    grond = grond_m.group(1).strip() if grond_m else ""

    terrein_m = re.search(r'<span>Terrein:</span>\s*<span>([^<]+)</span>', html)
    terrein = terrein_m.group(1).strip() if terrein_m else ""

    trefwoord_m = re.search(r'<span>Trefwoord:</span>\s*<span>([^<]+)</span>', html)
    trefwoord = trefwoord_m.group(1).strip() if trefwoord_m else ""

    # Extract samenvatting (summary)
    samenv_m = re.search(r'<div class="oordeel-samenvatting">(.*?)</div>', html, re.DOTALL)
    samenv_raw = samenv_m.group(1) if samenv_m else ""

    # Extract full decision text
    tekst_m = re.search(r'<div class="oordeel-tekst">(.*?)</div>\s*</div>', html, re.DOTALL)
    tekst_raw = tekst_m.group(1) if tekst_m else ""

    # Strip HTML tags and normalize whitespace
    def strip_html(s: str) -> str:
        s = re.sub(r'<[^>]+>', ' ', s)
        s = re.sub(r'&nbsp;', ' ', s)
        s = re.sub(r'&amp;', '&', s)
        s = re.sub(r'&lt;', '<', s)
        s = re.sub(r'&gt;', '>', s)
        s = re.sub(r'&#\d+;', '', s)
        return re.sub(r'\s+', ' ', s).strip()

    samenv_text = strip_html(samenv_raw)
    tekst_text  = strip_html(tekst_raw)

    # Combine: samenvatting + full text
    full_text = "\n\n".join(part for part in [samenv_text, tekst_text] if len(part) > 50)
    if len(full_text) < 100:
        return None

    return {
        "id":               f"mensenrechten-{year}-{number:04d}",
        "title":            title,
        "text":             full_text,
        "date":             date,
        "year":             year,
        "number":           number,
        "discriminatiegrond": grond,
        "terrein":          terrein,
        "trefwoord":        trefwoord,
        "url":              url,
        "source":           SOURCE,
        "license":          LICENSE,
        "country":          COUNTRY,
        "domain":           DOMAIN,
    }


def download(output_dir: Path, max_docs: int = 10_000, dry_run: bool = False) -> None:
    """Download all oordelen from oordelen.mensenrechten.nl."""
    output_dir.mkdir(parents=True, exist_ok=True)
    acq_date = datetime.date.today().isoformat()
    end_year  = datetime.date.today().year + 1

    if dry_run:
        logger.info("[mensenrechten] [dry-run] would download oordelen from %s", _BASE)
        return

    out_path = output_dir / "oordelen.jsonl"

    # Resume: collect already-downloaded IDs
    seen_ids: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen_ids.add(json.loads(line)["id"])
            except (KeyError, json.JSONDecodeError):
                pass
        logger.info("[mensenrechten] Resuming — %d oordelen already downloaded", len(seen_ids))

    total_saved = len(seen_ids)

    with out_path.open("a", encoding="utf-8") as f:
        for year in range(_START_YEAR, end_year):
            consecutive_miss = 0
            year_saved = 0

            for n in range(1, _MAX_PER_YEAR + 1):
                if total_saved >= max_docs:
                    break

                oordeel_id = f"mensenrechten-{year}-{n:04d}"
                if oordeel_id in seen_ids:
                    consecutive_miss = 0
                    continue

                url = f"{_BASE}/oordeel/{year}-{n:04d}"
                html = _fetch(url)
                time.sleep(_DELAY)

                if html is None:
                    consecutive_miss += 1
                    if consecutive_miss >= _MAX_MISS:
                        break
                    continue

                doc = _parse_oordeel(html, url, year, n)
                if doc is None:
                    consecutive_miss += 1
                    if consecutive_miss >= _MAX_MISS:
                        break
                    continue

                consecutive_miss = 0
                doc["acquisition_date"] = acq_date
                seen_ids.add(oordeel_id)
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                total_saved += 1
                year_saved  += 1

            logger.info("[mensenrechten] Year %d: %d new oordelen (total=%d)", year, year_saved, total_saved)

            if total_saved >= max_docs:
                logger.info("[mensenrechten] Reached max_docs=%d", max_docs)
                break

    # Acquisition log
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        entry = {
            "source":           SOURCE,
            "url":              _BASE,
            "license":          LICENSE,
            "acquisition_date": acq_date,
            "total_documents":  total_saved,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[mensenrechten] Done — %d oordelen saved to %s", total_saved, out_path)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download equality body decisions from College voor de Rechten van de Mens"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=10_000)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
