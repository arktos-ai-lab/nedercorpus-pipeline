"""Download Dutch Low Saxon (nds-nl) Wikipedia dump. License: CC-BY-SA.

Covers all major Dutch Low Saxon dialects: Gronings, Drents, Twents,
Stellingwerfs, Achterhoeks, Veluws, Sallands — spoken in the northeastern
and eastern Netherlands.
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/nds_nlwiki/latest/nds_nlwiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("low_saxon_wiki", DUMP_URL, "nds_nlwiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
