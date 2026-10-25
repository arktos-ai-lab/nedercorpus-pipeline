"""
Download Dutch dialect transcriptions from the Meertens NDB (Nederlandse Dialectenbank).

The Meertens Instituut maintains the NDB Soundbites collection at
meertens.knaw.nl/ndb/ — 772 transcribed audio fragments from Dutch dialect
recordings made in the mid-20th century.  Each fragment has a verbatim
transcription in the local dialect (place-name, Kloeke code, province).

These constitute authentic mid-century dialect text, not reconstructed or
normalised, making them valuable for dialect coverage in NederCorpus.

Strategy
--------
1. Try the NDB result/search endpoint with pagination:
   https://meertens.knaw.nl/ndb/result.php?transcriptie=true&start=N&max=100
   Parse HTML for transcript divs, location, Kloeke code, year.

2. Fallback: try the NDB index page for location/province links, then
   follow each location page to harvest individual soundbite records.

3. If the NDB website is unreachable or returns no data, log WARNING and exit
   gracefully — this source is best-effort.

Each soundbite is saved as a JSON line in ``soundbites.jsonl``::

    {"id": "...", "text": "...", "location": "...", "province": "...",
     "kloeke": "...", "year": ..., "url": "..."}

Individual text files are also written (``ndb_{id}.txt``) for soundbites with
a non-trivial transcript (≥ 20 characters after stripping whitespace).

License: research/educational use as documented by Meertens Instituut (KNAW)
Country: NL
Domain:  dialect
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

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

logger = logging.getLogger(__name__)

SOURCE  = "meertens_dialecten"
LICENSE = "CC-BY"    # Meertens / KNAW publications are CC BY unless stated otherwise
COUNTRY = "NL"
DOMAIN  = "dialect"
LANG    = "NL"

_BASE           = "https://meertens.knaw.nl/ndb"
_RESULT_URL     = _BASE + "/result.php?transcriptie=true&start={start}&max={page_size}"
_INDEX_URL      = _BASE + "/index.php"
_USER_AGENT     = (
    "NederCorpus/0.1 (academic research; Dutch dialect corpus; "
    "meertens.knaw.nl/ndb/)"
)
_DELAY          = 0.5   # seconds between HTTP requests
_MIN_TEXT_LEN   = 20    # minimum transcript length for individual .txt file

# ---------------------------------------------------------------------------
# HTML parsing helpers (no third-party libs)
# ---------------------------------------------------------------------------

# Remove all HTML/XML tags, collapsing whitespace
_TAG_RE      = re.compile(r"<[^>]+>", re.DOTALL)
_MULTI_WS_RE = re.compile(r"\s+")
# Named/numeric HTML entities we encounter in Dutch text
_ENTITIES: dict[str, str] = {
    "&amp;":   "&",
    "&lt;":    "<",
    "&gt;":    ">",
    "&quot;":  '"',
    "&apos;":  "'",
    "&nbsp;":  " ",
    "&ndash;": "–",
    "&mdash;": "—",
    "&lsquo;": "\u2018",
    "&rsquo;": "\u2019",
    "&ldquo;": "\u201c",
    "&rdquo;": "\u201d",
    "&ij;":    "ij",
    "&IJ;":    "IJ",
}
_ENTITY_RE = re.compile(r"&(?:#(\d+)|#x([0-9a-fA-F]+)|(\w+));")


def _unescape(text: str) -> str:
    """Replace HTML entities with their Unicode equivalents."""
    def _replace(m: re.Match) -> str:
        dec, hexa, name = m.group(1), m.group(2), m.group(3)
        if dec:
            return chr(int(dec))
        if hexa:
            return chr(int(hexa, 16))
        named = f"&{name};"
        return _ENTITIES.get(named, named)
    return _ENTITY_RE.sub(_replace, text)


def _strip_tags(html: str) -> str:
    """Strip all HTML tags and normalise whitespace."""
    text = _TAG_RE.sub(" ", html)
    text = _unescape(text)
    return _MULTI_WS_RE.sub(" ", text).strip()


def _inner_html(html: str, tag: str, class_: str | None = None) -> list[str]:
    """
    Return the inner HTML of all ``<tag>`` elements, optionally filtered by
    a CSS class attribute (substring match, case-insensitive).

    Uses a simple regex — works for non-nested, moderately complex HTML.
    """
    if class_:
        open_re = re.compile(
            rf"<{re.escape(tag)}[^>]*class=['\"][^'\"]*{re.escape(class_)}[^'\"]*['\"][^>]*>",
            re.IGNORECASE | re.DOTALL,
        )
    else:
        open_re = re.compile(rf"<{re.escape(tag)}(?:\s[^>]*)?>", re.IGNORECASE | re.DOTALL)

    close_tag = f"</{tag}>"
    results   = []
    for m in open_re.finditer(html):
        start = m.end()
        end   = html.lower().find(close_tag.lower(), start)
        if end == -1:
            continue
        results.append(html[start:end])
    return results


def _attr(html_tag: str, name: str) -> str:
    """Extract the value of an attribute from a single HTML tag snippet."""
    m = re.search(
        rf"""{re.escape(name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))""",
        html_tag, re.IGNORECASE,
    )
    if not m:
        return ""
    return m.group(1) or m.group(2) or m.group(3) or ""


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes | None:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                logger.debug("[meertens_dialecten] HTTP %d for %s", exc.code, url)
                return None
            if attempt == retries - 1:
                logger.warning("[meertens_dialecten] HTTP %d for %s", exc.code, url)
                return None
            time.sleep(2.0 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[meertens_dialecten] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 ** attempt)
    return None


def _log(output_dir: Path, url: str, identifier: str) -> None:
    entry = {
        "source":           SOURCE,
        "url":              url,
        "license":          LICENSE,
        "identifier":       identifier,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# HTML parsing for NDB result pages
# ---------------------------------------------------------------------------

def _parse_result_page(html: str, page_url: str) -> list[dict]:
    """
    Parse one NDB result page and return a list of soundbite dicts.

    The NDB result page structure (as of 2024) renders each soundbite inside
    a ``<div class="resultaat">`` or ``<tr>`` table row.  We attempt multiple
    extraction patterns and take whatever yields text.
    """
    records: list[dict] = []

    # --- Strategy A: look for <div class="resultaat"> blocks ---
    blocks = _inner_html(html, "div", "resultaat")
    if not blocks:
        # --- Strategy B: look for <tr> rows with at least one td ---
        table_bodies = _inner_html(html, "tbody")
        for tb in table_bodies:
            blocks.extend(_inner_html(tb, "tr"))

    for i, block in enumerate(blocks):
        # Extract transcript text — try several class names
        text = ""
        for cls in ("transcriptie", "transcript", "tekst", "text"):
            spans = _inner_html(block, "span", cls) or _inner_html(block, "div", cls)
            if spans:
                text = _strip_tags(spans[0])
                break
        if not text:
            # Fallback: second <td> often holds the transcript
            tds = _inner_html(block, "td")
            if len(tds) >= 2:
                text = _strip_tags(tds[1])

        if not text or len(text) < 3:
            continue

        # Extract location / place name
        location = ""
        for cls in ("plaats", "location", "plaatsnaam"):
            spans = _inner_html(block, "span", cls) or _inner_html(block, "td", cls)
            if spans:
                location = _strip_tags(spans[0])
                break
        if not location:
            tds = _inner_html(block, "td")
            if tds:
                location = _strip_tags(tds[0])

        # Extract province
        province = ""
        for cls in ("provincie", "province"):
            spans = _inner_html(block, "span", cls) or _inner_html(block, "td", cls)
            if spans:
                province = _strip_tags(spans[0])
                break

        # Extract Kloeke code
        kloeke = ""
        for cls in ("kloeke", "kloeke_code"):
            spans = _inner_html(block, "span", cls) or _inner_html(block, "td", cls)
            if spans:
                kloeke = _strip_tags(spans[0])
                break
        if not kloeke:
            m = re.search(r"\b([A-Z]\d{3}[a-z]?)\b", block)
            if m:
                kloeke = m.group(1)

        # Extract year
        year: int | None = None
        m = re.search(r"\b(19[0-9]{2}|20[0-2][0-9])\b", block)
        if m:
            year = int(m.group(1))

        # Build a stable ID from kloeke + running index (no database IDs exposed)
        soundbite_id = f"{kloeke}_{i:04d}" if kloeke else f"ndb_{i:04d}"

        # --- Linguistic Rule: Kloeke code is the anchor for dialect rules ---
        ledger = []
        if kloeke:
            ledger.append({
                "entry_type": "linguistic_rule",
                "evidence_pointer": f"Meertens:Kloeke:{kloeke}",
                "method": "deterministic",
                "metadata": {"location": location, "province": province}
            })

        # Extract a link for this soundbite if present
        href_m = re.search(r'href=["\']([^"\']*detail[^"\']*)["\']', block, re.IGNORECASE)
        item_url = (
            urllib.parse.urljoin(page_url, href_m.group(1))
            if href_m
            else page_url
        )

        records.append({
            "id":       soundbite_id,
            "text":     text,
            "location": location,
            "province": province,
            "kloeke":   kloeke,
            "year":     year,
            "url":      item_url,
            "ledger":   ledger,
        })

    return records


# ---------------------------------------------------------------------------
# Strategy 1: paginated result.php endpoint
# ---------------------------------------------------------------------------

def _collect_via_result_php(max_items: int, page_size: int = 100) -> list[dict]:
    """
    Fetch NDB result pages with transcriptie=true, paginating by start offset.
    Returns up to max_items records.
    """
    all_records: list[dict] = []
    start = 0

    while len(all_records) < max_items:
        url  = _RESULT_URL.format(start=start, page_size=page_size)
        logger.info("[meertens_dialecten] result.php start=%d", start)
        data = _fetch(url, timeout=30)
        if data is None:
            logger.warning(
                "[meertens_dialecten] result.php unreachable at start=%d", start
            )
            break

        try:
            html = data.decode("utf-8", errors="replace")
        except Exception:
            html = data.decode("latin-1", errors="replace")

        # Quick sanity check: does the page look like an NDB page?
        if "meertens" not in html.lower() and "ndb" not in html.lower():
            logger.warning(
                "[meertens_dialecten] result.php returned unexpected content at start=%d",
                start,
            )
            break

        page_records = _parse_result_page(html, url)
        if not page_records:
            logger.info(
                "[meertens_dialecten] result.php: no records parsed at start=%d — stopping",
                start,
            )
            break

        all_records.extend(page_records)
        logger.info(
            "[meertens_dialecten] result.php: +%d records (total %d)",
            len(page_records), len(all_records),
        )

        if len(page_records) < page_size:
            logger.info("[meertens_dialecten] result.php: last page reached")
            break

        start += page_size
        time.sleep(_DELAY)

    return all_records[:max_items]


# ---------------------------------------------------------------------------
# Strategy 2: index page → location links → detail pages
# ---------------------------------------------------------------------------

def _collect_via_index(max_items: int) -> list[dict]:
    """
    Fetch the NDB index page, extract location/province links, then follow
    each to harvest soundbite records.  Stops after max_items records or
    when all location pages have been visited.
    """
    logger.info("[meertens_dialecten] Fetching NDB index page …")
    data = _fetch(_INDEX_URL, timeout=30)
    if data is None:
        logger.warning("[meertens_dialecten] NDB index page unreachable")
        return []

    try:
        index_html = data.decode("utf-8", errors="replace")
    except Exception:
        index_html = data.decode("latin-1", errors="replace")

    # Extract all links that look like location or province pages
    link_re = re.compile(
        r'href=["\']([^"\']*(?:provincie|regio|plaats|location|kloeke)[^"\']*)["\']',
        re.IGNORECASE,
    )
    raw_links = list(dict.fromkeys(  # deduplicate, preserve order
        urllib.parse.urljoin(_INDEX_URL, m.group(1))
        for m in link_re.finditer(index_html)
    ))

    if not raw_links:
        # Fallback: grab all internal hrefs
        raw_links = list(dict.fromkeys(
            urllib.parse.urljoin(_INDEX_URL, m.group(1))
            for m in re.finditer(r'href=["\']([^"\']+)["\']', index_html)
            if _BASE in urllib.parse.urljoin(_INDEX_URL, m.group(1))
        ))

    logger.info("[meertens_dialecten] Index: found %d location links", len(raw_links))

    all_records: list[dict] = []

    for link_url in raw_links:
        if len(all_records) >= max_items:
            break
        logger.info("[meertens_dialecten] Fetching location page: %s", link_url)
        page_data = _fetch(link_url, timeout=30)
        if page_data is None:
            continue

        try:
            page_html = page_data.decode("utf-8", errors="replace")
        except Exception:
            page_html = page_data.decode("latin-1", errors="replace")

        page_records = _parse_result_page(page_html, link_url)
        if page_records:
            all_records.extend(page_records)
            logger.info(
                "[meertens_dialecten] +%d from %s (total %d)",
                len(page_records), link_url, len(all_records),
            )
        time.sleep(_DELAY)

    return all_records[:max_items]


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

def download(
    output_dir: Path,
    max_items: int = 1_000,
    dry_run: bool = False,
    **kwargs,
) -> None:
    """
    Download Dutch dialect transcriptions from the Meertens NDB.

    Records are saved to ``soundbites.jsonl``.  Individual text files
    (``ndb_{id}.txt``) are written for transcripts ≥ 20 characters.

    Acquisition proceeds in two stages:
    1. Paginated ``result.php`` endpoint (primary).
    2. Index page → location links (fallback if primary returns nothing).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.info("[meertens_dialecten] [dry-run] skipping")
        return

    # --- Load already-collected IDs to support idempotent re-runs ---
    index_path = output_dir / "soundbites.jsonl"
    seen_ids: set[str] = set()
    if index_path.exists():
        with index_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("id"):
                        seen_ids.add(rec["id"])
                except json.JSONDecodeError:
                    pass
    logger.info("[meertens_dialecten] %d existing records in soundbites.jsonl", len(seen_ids))

    # --- Primary: result.php pagination ---
    logger.info("[meertens_dialecten] Strategy 1: result.php pagination …")
    records = _collect_via_result_php(max_items)

    if not records:
        logger.warning(
            "[meertens_dialecten] result.php returned no data; "
            "trying index page fallback …"
        )
        records = _collect_via_index(max_items)

    if not records:
        logger.warning(
            "[meertens_dialecten] No dialect transcriptions found. "
            "The NDB website may be temporarily unavailable or its HTML structure "
            "may have changed. Check https://meertens.knaw.nl/ndb/ manually."
        )
        return

    logger.info("[meertens_dialecten] Collected %d candidate records", len(records))

    total   = 0
    skipped = 0

    with index_path.open("a", encoding="utf-8") as jf:
        for record in records:
            record_id = record.get("id", "")
            if not record_id:
                continue

            if record_id in seen_ids:
                skipped += 1
                continue

            text = record.get("text", "").strip()
            if not text:
                skipped += 1
                continue

            # Write to soundbites.jsonl
            jf.write(json.dumps(record, ensure_ascii=False) + "\n")
            seen_ids.add(record_id)

            # Write individual .txt file for non-trivial transcripts
            if len(text) >= _MIN_TEXT_LEN:
                txt_path = output_dir / f"ndb_{record_id}.txt"
                if not txt_path.exists():
                    txt_path.write_text(text, encoding="utf-8")

            _log(output_dir, record.get("url", _BASE), record_id)
            total += 1

    logger.info(
        "[meertens_dialecten] Done. %d soundbites saved, %d skipped.",
        total, skipped,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Download Dutch dialect transcriptions from the Meertens NDB "
            "(meertens.knaw.nl/ndb/)"
        )
    )
    parser.add_argument("--output",    required=True, type=Path,
                        help="Directory to save soundbites.jsonl and .txt files")
    parser.add_argument("--max-items", type=int, default=1_000,
                        help="Maximum number of soundbites to collect (default: 1000)")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Log actions without writing any files")
    args = parser.parse_args()
    download(args.output, max_items=args.max_items, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
