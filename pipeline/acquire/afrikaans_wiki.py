"""Download Afrikaans (af) Wikipedia dump. License: CC-BY-SA. Domain: encyclopedic.

Afrikaans is a West Germanic language descended directly from 17th-century Dutch
(Cape Dutch). It is closely related to Dutch and is co-included in NederCorpus
as a Dutch-family language variant.
"""
from pathlib import Path
from pipeline.acquire._wikimedia import download_dump

DUMP_URL = "https://dumps.wikimedia.org/afwiki/latest/afwiki-latest-pages-articles.xml.bz2"

def download(output_dir: Path, dry_run: bool = False) -> None:
    download_dump("afrikaans_wiki", DUMP_URL, "afwiki-latest-pages-articles.xml.bz2",
                  output_dir, license_="CC-BY-SA", dry_run=dry_run)
