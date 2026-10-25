"""Download Dutch Wikiversity dump. License: CC-BY-SA. Domain: educational."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/nlwikiversity/latest/nlwikiversity-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("wikiversity", DUMP_URL, "nlwikiversity-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
