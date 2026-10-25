"""Download Zeelandic (zea) Wikipedia dump. License: CC-BY-SA. Domain: encyclopedic."""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/zeawiki/latest/zeawiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("zeelandic_wiki", DUMP_URL, "zeawiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
