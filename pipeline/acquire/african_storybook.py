"""
Download Afrikaans and Dutch children's stories from the African Storybook Project (ASP).

Sources:
  AF (Afrikaans): https://github.com/global-asp/asp-source  af/ folder
  NL (Dutch):     https://github.com/global-asp/global-asp  nl/ folder

Both use the same Markdown format: # Title, ## page separators, and a footer
  * License: [CC-BY]
  * Text: Author
  * Illustration: Illustrator

Only CC-BY stories are kept; CC-BY-NC variants are excluded.

Output:
  stories/{lang}_{slug_safe}.md  — raw Markdown per story
  metadata.jsonl                 — one JSON record per story

License: CC-BY (per story; NC filtered out)
Languages: AF (Afrikaans, country=ZA), NL (Dutch, country=NL)
Domain: literature (children's fiction)

Usage:
  python -m pipeline acquire --sources african_storybook
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
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "african_storybook"
LICENSE = "CC-BY"
COUNTRY = "ZA"
DOMAIN  = "literature"
LANG    = "AF"

_REPOS: list[dict] = [
    {
        "api_url": "https://api.github.com/repos/global-asp/asp-source/contents/af",
        "lang":    "AF",
        "country": "ZA",
    },
    {
        "api_url": "https://api.github.com/repos/global-asp/global-asp/contents/nl",
        "lang":    "NL",
        "country": "NL",
    },
]
_UA = "NederCorpus/0.1 (academic corpus; ASP children's stories)"
_DELAY = 0.5  # seconds between requests

# ASP license line format: "* License: [CC-BY]" or "* License: [CC-BY 4.0]"
_LICENSE_LINE = re.compile(r'^\*\s*[Ll]icense:\s*\[(.+?)\]', re.MULTILINE)
_NC_ND_PAT    = re.compile(r'\bnc\b|\bnd\b|noncommercial|no.deriv', re.IGNORECASE)


def _fetch(url: str, timeout: int = 30, retries: int = 3) -> bytes | None:
    headers = {"User-Agent": _UA, "Accept": "application/vnd.github.v3+json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404) and attempt == retries - 1:
                logger.warning("[african_storybook] HTTP %d: %s", exc.code, url)
                return None
            # Respect GitHub rate limit
            if exc.code == 403:
                time.sleep(60)
            else:
                time.sleep(2.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[african_storybook] Failed %s: %s", url, exc)
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def _parse_asp_story(text: str) -> tuple[str, str, str]:
    """Parse an ASP Markdown story file.

    ASP asp-source stories use this format (NO YAML front matter):
      # Title
      ## (page separator)
      Page text
      ## (page separator)
      Page text
      * License: [CC-BY]
      * Text: Author Name
      * Illustration: Illustrator Name

    Returns (title, story_text, license_str).
    """
    lines = text.splitlines()

    # Extract title from first `# Heading` line
    title = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break

    # Extract license from footer bullet: "* License: [CC-BY]"
    license_str = ""
    m = _LICENSE_LINE.search(text)
    if m:
        license_str = m.group(1).strip()

    # Extract story text: everything between ## separators, minus metadata bullets
    story_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip heading (title line)
        if stripped.startswith("# "):
            continue
        # Skip page separator markers
        if stripped == "##":
            continue
        # Stop at footer metadata bullets
        if stripped.startswith("* ") and re.match(r'^\* [A-Z][a-z]+:', stripped):
            continue
        if stripped:
            story_lines.append(stripped)

    return title, "\n".join(story_lines), license_str


def _fetch_repo_stories(
    repo_info: dict,
    stories_dir: Path,
    metadata_path: Path,
    seen: set[str],
    acq_date: str,
) -> tuple[int, int, int]:
    """Fetch all CC-BY stories from one GitHub repo/folder. Returns (saved, skipped_nc, skipped_short)."""
    lang    = repo_info["lang"]
    country = repo_info["country"]
    api_url = repo_info["api_url"]

    logger.info("[african_storybook] Fetching %s stories from %s", lang, api_url)
    raw = _fetch(api_url)
    if raw is None:
        logger.error("[african_storybook] Cannot fetch listing for %s — skipping", lang)
        return 0, 0, 0

    try:
        listing = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[african_storybook] JSON parse error for %s: %s", lang, exc)
        return 0, 0, 0

    if not isinstance(listing, list):
        logger.error("[african_storybook] Unexpected listing type for %s", lang)
        return 0, 0, 0

    md_files = [
        item for item in listing
        if isinstance(item, dict)
        and item.get("type") == "file"
        and item.get("name", "").endswith(".md")
    ]
    logger.info("[african_storybook] Found %d .md files for %s", len(md_files), lang)

    saved = skipped_nc = skipped_short = 0

    with metadata_path.open("a", encoding="utf-8") as fmeta:
        for item in md_files:
            # Use lang prefix to avoid slug collisions between AF and NL
            slug = f"{lang.lower()}_{item['name'][:-3]}"
            if slug in seen:
                continue

            download_url = item.get("download_url") or ""
            if not download_url:
                continue

            content_bytes = _fetch(download_url)
            if content_bytes is None:
                continue

            try:
                content = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue

            title, story_text, license_str = _parse_asp_story(content)

            if _NC_ND_PAT.search(license_str):
                skipped_nc += 1
                continue
            if not license_str or "cc" not in license_str.lower():
                skipped_nc += 1
                continue

            if len(story_text) < 20:
                skipped_short += 1
                continue

            safe_name = f"{lang.lower()}_{re.sub(r'[^\w.\-]', '_', item['name'])}"
            story_path = stories_dir / safe_name
            story_path.write_bytes(content_bytes)

            record = {
                "slug":             slug,
                "safe_name":        safe_name,
                "title":            title or slug,
                "license":          license_str,
                "source_url":       item.get("html_url", download_url),
                "language":         lang,
                "country":          country,
                "acquisition_date": acq_date,
                "text_length":      len(story_text),
            }
            fmeta.write(json.dumps(record, ensure_ascii=False) + "\n")
            seen.add(slug)
            saved += 1

            time.sleep(_DELAY)

    return saved, skipped_nc, skipped_short


def download(output_dir: Path, dry_run: bool = False) -> None:
    """
    Download AF (Afrikaans) and NL (Dutch) ASP children's stories from GitHub.

    Saves:
      stories/{lang}_{slug_safe}.md  — raw markdown per story
      metadata.jsonl                 — one JSON record per story
      acquisition_log.jsonl          — run log
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stories_dir = output_dir / "stories"
    stories_dir.mkdir(exist_ok=True)
    metadata_path = output_dir / "metadata.jsonl"
    acq_date = datetime.date.today().isoformat()

    if dry_run:
        for repo in _REPOS:
            logger.info(
                "[african_storybook] [dry-run] would fetch %s stories from %s",
                repo["lang"], repo["api_url"],
            )
        return

    # Load already-saved slugs for resume
    seen: set[str] = set()
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen.add(json.loads(line)["slug"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen:
            logger.info("[african_storybook] Resuming — %d stories already saved", len(seen))

    total_saved = total_nc = total_short = 0

    for repo in _REPOS:
        s, nc, sh = _fetch_repo_stories(repo, stories_dir, metadata_path, seen, acq_date)
        total_saved += s
        total_nc    += nc
        total_short += sh

    # Acquisition log
    entry = {
        "source":           SOURCE,
        "url":              [r["api_url"] for r in _REPOS],
        "license":          LICENSE,
        "acquisition_date": acq_date,
        "saved":            total_saved,
        "skipped_nc":       total_nc,
        "skipped_short":    total_short,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(
        "[african_storybook] Done — %d stories saved (%d NC/unknown, %d too short)",
        total_saved, total_nc, total_short,
    )


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Download Afrikaans ASP stories from GitHub (CC-BY 4.0)"
    )
    parser.add_argument("--output",  required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    download(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
