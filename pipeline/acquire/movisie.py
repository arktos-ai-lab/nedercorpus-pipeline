"""
Download publications from Movisie (Netherlands Centre for Social Development).

Movisie publishes ~1,190 open-access Dutch-language publications on social
policy, welfare, civic participation, and care. All publications are
licensed CC-BY 4.0.

Discovery strategy:
  Paginate /zoeken?f[0]=npt:publication&page=N (Drupal search, ~10 per page)
  Each result links to /publicatie/{slug}
  Fetch each publication page and extract article text

License: CC BY 4.0  https://www.movisie.nl/copyright
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

SOURCE  = "movisie"
LICENSE = "CC-BY"
COUNTRY = "NL"
DOMAIN  = "policy"

_BASE_URL   = "https://www.movisie.nl"
_SEARCH_URL = _BASE_URL + "/zoeken"
_USER_AGENT = "NederCorpus/0.1 (academic research; Dutch social-policy corpus; CC-BY)"
_DELAY      = 1.0   # seconds between requests

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# Match /publicatie/<slug> hrefs
_PUB_RE = re.compile(r'href="(/publicatie/[^"?#]+)"')

# Simple HTML tag stripper
_TAG_RE  = re.compile(r"<[^>]+>")
_ENT_RE  = re.compile(r"&([a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);")

_HTML_ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "nbsp": " ", "ndash": "\u2013", "mdash": "\u2014", "lsquo": "\u2018",
    "rsquo": "\u2019", "ldquo": "\u201c", "rdquo": "\u201d",
    "euro": "\u20ac", "hellip": "\u2026",
}


def _decode_html(text: str) -> str:
    def _repl(m: re.Match) -> str:
        ref = m.group(1)
        if ref.startswith("#x"):
            return chr(int(ref[2:], 16))
        if ref.startswith("#"):
            return chr(int(ref[1:]))
        return _HTML_ENTITIES.get(ref, m.group(0))
    return _ENT_RE.sub(_repl, text)


def _strip_tags(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _decode_html(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch(url: str, timeout: int = 20, retries: int = 3) -> bytes:
    headers = {
        "User-Agent":      _USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "nl,nl-NL;q=0.9",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410, 403):
                raise
            if attempt == retries - 1:
                raise
            time.sleep(3.0 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _collect_pub_slugs(output_dir: Path, max_pages: int = 200) -> list[str]:
    """Paginate search results and return unique /publicatie/ slugs."""
    seen: set[str] = set()
    slugs: list[str] = []

    # Try to resume from checkpoint
    checkpoint = output_dir / "slugs.json"
    if checkpoint.exists():
        try:
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            slugs = saved.get("slugs", [])
            seen  = set(slugs)
            start_page = saved.get("last_page", 0) + 1
            logger.info("[movisie] Resuming slug collection from page %d (%d slugs)", start_page, len(slugs))
        except Exception:
            start_page = 0
    else:
        start_page = 0

    for page in range(start_page, max_pages):
        params = urllib.parse.urlencode({"f[0]": "npt:publication", "page": page})
        url = f"{_SEARCH_URL}?{params}"
        logger.debug("[movisie] Search page %d: %s", page, url)

        try:
            html = _fetch(url).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                logger.info("[movisie] Search exhausted at page %d (404)", page)
                break
            logger.warning("[movisie] Search page %d failed: %s", page, exc)
            break
        except Exception as exc:
            logger.warning("[movisie] Search page %d failed: %s", page, exc)
            break

        # Check for empty results page
        if "Geen resultaten gevonden" in html or "/publicatie/" not in html:
            logger.info("[movisie] Search exhausted at page %d (no results)", page)
            break

        new_this_page = 0
        for m in _PUB_RE.finditer(html):
            slug = m.group(1)
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
                new_this_page += 1

        logger.info("[movisie] Page %d: %d new slugs (total %d)", page, new_this_page, len(slugs))

        # Save checkpoint every 10 pages
        if page % 10 == 0:
            checkpoint.write_text(
                json.dumps({"slugs": slugs, "last_page": page}, ensure_ascii=False),
                encoding="utf-8",
            )

        if new_this_page == 0:
            logger.info("[movisie] No new slugs on page %d — stopping", page)
            break

        time.sleep(_DELAY)

    # Final checkpoint
    checkpoint.write_text(
        json.dumps({"slugs": slugs, "last_page": page}, ensure_ascii=False),
        encoding="utf-8",
    )
    return slugs


def _extract_pub(html: str, url: str) -> dict | None:
    """Extract title, date, and body text from a publication page."""
    # Title: <h1 ...>...</h1>
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = _strip_tags(title_m.group(1)).strip() if title_m else ""

    # Date: look for datetime attribute or text in a time element
    date_m = re.search(r'<time[^>]+datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})"', html)
    if not date_m:
        date_m = re.search(r'"datePublished"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})', html)
    pub_date = date_m.group(1) if date_m else ""

    # Main article body: <article ...>...</article> or <main ...>...</main>
    body_m = re.search(r"<article\b[^>]*>(.*?)</article>", html, re.S)
    if not body_m:
        body_m = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S)
    if not body_m:
        return None

    body_html = body_m.group(1)

    # Remove script/style/nav blocks
    body_html = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", body_html, flags=re.S | re.I)

    text = _strip_tags(body_html)
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < 200:
        return None

    return {
        "title":    title,
        "text":     text,
        "url":      url,
        "date":     pub_date,
        "source":   SOURCE,
        "license":  LICENSE,
        "country":  COUNTRY,
        "domain":   DOMAIN,
        "language": "nl",
    }


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download all Movisie publications.

    Phase 1: collect /publicatie/ slugs by paginating search results.
    Phase 2: fetch each publication page and save as JSONL.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[movisie] [dry-run] would download ~1,190 Movisie publications")
        return

    # Phase 1: collect slugs
    logger.info("[movisie] Phase 1: collecting publication slugs...")
    slugs = _collect_pub_slugs(output_dir)
    logger.info("[movisie] Phase 1 done: %d publication slugs", len(slugs))

    if not slugs:
        logger.error("[movisie] No slugs found — check if movisie.nl changed structure")
        return

    _log(output_dir, _SEARCH_URL)

    # Phase 2: fetch publication pages
    out_file  = output_dir / "movisie_publications.jsonl"
    done_file = output_dir / "done_slugs.txt"

    done: set[str] = set()
    if done_file.exists():
        done = set(done_file.read_text(encoding="utf-8").splitlines())

    todo = [s for s in slugs if s not in done]
    logger.info("[movisie] Phase 2: %d publications to fetch (%d already done)", len(todo), len(done))

    fetched = 0
    with out_file.open("a", encoding="utf-8") as fout:
        for i, slug in enumerate(todo):
            url = _BASE_URL + slug
            try:
                html = _fetch(url).decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                logger.debug("[movisie] Skip %s: HTTP %d", slug, exc.code)
                done.add(slug)
                continue
            except Exception as exc:
                logger.warning("[movisie] Failed %s: %s", slug, exc)
                continue

            record = _extract_pub(html, url)
            if record:
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                fetched += 1

            done.add(slug)

            if i % 100 == 0:
                done_file.write_text("\n".join(done), encoding="utf-8")
                logger.info("[movisie] Phase 2: %d/%d fetched", fetched, i + 1)

            time.sleep(_DELAY)

    done_file.write_text("\n".join(done), encoding="utf-8")
    logger.info("[movisie] Phase 2 done: %d publications saved", fetched)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download Movisie publications (CC-BY 4.0)")
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
