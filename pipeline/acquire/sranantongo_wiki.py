"""Download Sranan Tongo (srn) Wikipedia dump. License: CC-BY-SA.

Sranan Tongo is an English-based creole with strong Dutch, Portuguese, and West
African influence, spoken in Suriname — a former Dutch colony. It is the most
widely spoken language in Suriname alongside Dutch.
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/srnwiki/latest/srnwiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("sranantongo_wiki", DUMP_URL, "srnwiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
