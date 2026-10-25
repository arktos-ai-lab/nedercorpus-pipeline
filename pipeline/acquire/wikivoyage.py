"""Download Dutch Wikivoyage dump. License: CC-BY-SA. Domain: travel."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/nlwikivoyage/latest/nlwikivoyage-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("wikivoyage", DUMP_URL, "nlwikivoyage-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
