"""Download West Frisian (fy) Wikibooks dump. License: CC-BY-SA. Domain: educational."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/fywikibooks/latest/fywikibooks-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("frisian_wikibooks", DUMP_URL, "fywikibooks-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
