"""
Download Flemish legislation from the Vlaamse Codex.

The Vlaamse Codex is the official publication of Flemish legislation,
maintained by the Flemish Government.  Its open-data API provides
machine-readable access to 39,000+ legislative documents.

API: https://codex.opendata.api.vlaanderen.be/swagger/docs/v1
     Base URL: https://codex.opendata.api.vlaanderen.be

Phase 1: Paginate /api/WetgevingDocument?taal=nl&skip=N&limit=500 (returns
         ResultatenLijst with TotaalAantal=39,397).
Phase 2: For each doc with HeeftInhoud=true, fetch article text via:
         /api/WetgevingDocument/{id}/ArtikelversieRelaties
         → for each versie: /api/WetgevingArtikelVersie/{versie_id} → Tekst field
         (VolledigDocument endpoint exists but contains no article text.)

License: CC0 (Vlaamse Open Data)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "codex"
LICENSE = "CC0"
COUNTRY = "BE"
DOMAIN  = "legislation"

CODEX_BASE = "https://codex.opendata.api.vlaanderen.be"
CODEX_PORTAL_URL = "https://codex.vlaanderen.be"


def _log(output_dir: Path, url: str) -> None:
    entry = {
        "source": SOURCE,
        "url": url,
        "license": LICENSE,
        "acquisition_date": datetime.date.today().isoformat(),
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {
        "User-Agent": "NederCorpus/0.1 (academic research; open government data)",
        "Accept": "application/json",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2.0 * (attempt + 1)
            logger.warning("Retry %d for %s: %s (wait %.1fs)", attempt + 1, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _strip_html(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s{2,}", " ", text).strip()


def _fetch_doc_text(doc_id: int) -> str:
    """
    Fetch all current article texts for a document via:
      GET /api/WetgevingDocument/{id}/ArtikelversieRelaties
      → for each current versie (EindDatum is None):
        GET /api/WetgevingArtikelVersie/{versie_id} → Tekst

    Returns combined plain text or empty string on failure.
    """
    parts: list[str] = []
    av_url = f"{CODEX_BASE}/api/WetgevingDocument/{doc_id}/ArtikelversieRelaties"
    try:
        raw = _fetch(av_url, timeout=30)
        data = json.loads(raw)
    except Exception as exc:
        logger.debug("ArtikelversieRelaties failed for %s: %s", doc_id, exc)
        return ""

    versies = data.get("ResultatenLijst") or []
    # Keep only current versies (no end date) to avoid duplicates from amendments
    current = [v for v in versies if not v.get("ArtikelVersieEindDatum")]
    if not current:
        current = versies  # fallback: take all if none are "current"

    for versie in current:
        versie_id = versie.get("WetgevingArtikelVersieId") or versie.get("Id")
        if not versie_id:
            continue
        try:
            v_url = f"{CODEX_BASE}/api/WetgevingArtikelVersie/{versie_id}"
            v_raw = _fetch(v_url, timeout=20)
            v_data = json.loads(v_raw)
            tekst = (v_data.get("Tekst") or "").strip()
            if tekst:
                parts.append(_strip_html(tekst))
        except Exception as exc:
            logger.debug("WetgevingArtikelVersie %s failed: %s", versie_id, exc)

    return "\n\n".join(p for p in parts if p)


def download(
    output_dir: Path,
    page_size: int = 500,
    max_docs: int = 50_000,
    dry_run: bool = False,
) -> None:
    """
    Download Flemish legislative documents from the Vlaamse Codex API.

    Phase 1: Paginate the WetgevingDocument list endpoint.
    Phase 2: Fetch VolledigDocument for each entry that has content (HeeftInhoud=true).

    Each document is saved as {id}.json with a .url sidecar.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    skip = 0
    total_fetched = 0

    logger.info("[codex] Starting download (max %d docs)", max_docs)

    while total_fetched < max_docs:
        list_url = f"{CODEX_BASE}/api/WetgevingDocument?taal=nl&skip={skip}&limit={page_size}"
        logger.info("[codex] Listing skip=%d — %s", skip, list_url)

        if dry_run:
            logger.info("[dry-run] skipping")
            break

        try:
            raw = _fetch(list_url)
            page = json.loads(raw)
        except Exception as exc:
            logger.warning("[codex] List request failed at skip=%d: %s", skip, exc)
            break

        items = (page.get("ResultatenLijst")
                 or page.get("WetgevingDocumenten")
                 or page.get("Items")
                 or [])
        if isinstance(page, list):
            items = page

        if not items:
            logger.info("[codex] No more items at skip=%d", skip)
            break

        logger.info("  %d items this page", len(items))

        for item in items:
            if total_fetched >= max_docs:
                break

            doc_id = item.get("Id") or item.get("id")
            heeft_inhoud = item.get("HeeftInhoud", True)

            if not doc_id:
                continue
            if not heeft_inhoud:
                continue

            safe_id = re.sub(r"[^\w\-]", "_", str(doc_id))
            dest = output_dir / f"{safe_id}.json"
            if dest.exists():
                total_fetched += 1
                continue

            # Fetch article texts via ArtikelversieRelaties → WetgevingArtikelVersie
            text = _fetch_doc_text(doc_id)
            if not text:
                logger.debug("  No text for doc %s, skipping", doc_id)
                time.sleep(0.2)
                continue

            canonical = f"{CODEX_PORTAL_URL}/Zoeken/Document.aspx?DID={doc_id}"

            doc_record = {
                "id": doc_id,
                "opschrift": item.get("Opschrift") or "",
                "type": item.get("WetgevingDocumentType") or "",
                "datum": item.get("Datum") or "",
                "text": text,
                "canonical": canonical,
                "acquisition_date": datetime.date.today().isoformat(),
            }

            dest.write_text(json.dumps(doc_record, ensure_ascii=False), encoding="utf-8")
            dest.with_name(dest.name + ".url").write_text(canonical, encoding="utf-8")
            _log(output_dir, canonical)
            total_fetched += 1

            time.sleep(0.2)

        skip += len(items)  # use actual count, not page_size (API caps at 200)

        # Stop when we've consumed all available documents
        total_available = page.get("TotaalAantal") or page.get("total") or 0
        if total_available and skip >= total_available:
            logger.info("[codex] Reached end of collection (%d/%d)", skip, total_available)
            break
        if not items:
            logger.info("[codex] Empty page at skip=%d, stopping", skip)
            break

        time.sleep(0.5)

    logger.info("[codex] Done. %d documents downloaded.", total_fetched)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Flemish legislation from the Vlaamse Codex (CC0)"
    )
    parser.add_argument("--output",   required=True, type=Path)
    parser.add_argument("--max-docs", type=int, default=50_000)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()
    download(args.output, max_docs=args.max_docs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
