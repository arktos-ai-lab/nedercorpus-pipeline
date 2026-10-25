"""
Download Dutch academic publications from university OAI-PMH repositories.

Institutions (all confirmed live 2026-04-22):
  Leiden University Scholarly Publications — scholarlypublications.universiteitleiden.nl/oai2
  UvA-DARE (University of Amsterdam)       — pure.uva.nl/ws/oai
  KU Leuven Lirias                         — lirias.kuleuven.be/oai
  Ghent University Institutional Archive   — biblio.ugent.be/oai

Harvests Dublin Core metadata, filters for Dutch-language records, and rejects
NC/ND licenses.  Title + abstract (description) becomes the corpus text.

Output:
  {output_dir}/leiden/records.jsonl
  {output_dir}/uva/records.jsonl
  {output_dir}/kuleuven/records.jsonl

License: CC-BY / CC-BY-SA / open access (per record)
Domain: academic   Languages: NL (Leiden, UvA), NL-BE (KU Leuven)

Usage:
  python -m pipeline acquire --sources university_repos_nl
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
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE  = "university_repos_nl"
LICENSE = "CC-BY"
DOMAIN  = "academic"

_UA = "NederCorpus/0.1 (academic corpus; Dutch OA research; contact: corpus@example.org)"
_DELAY = 1.5  # seconds between OAI page requests

_NS_OAI    = "http://www.openarchives.org/OAI/2.0/"
_NS_DC     = "http://purl.org/dc/elements/1.1/"
_NS_OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"

INSTITUTIONS: list[dict] = [
    {
        "name":    "leiden",
        "label":   "Leiden University Scholarly Publications",
        "oai_url": "https://scholarlypublications.universiteitleiden.nl/oai2",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "uva",
        "label":   "UvA-DARE (University of Amsterdam)",
        "oai_url": "https://pure.uva.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "kuleuven",
        "label":   "KU Leuven Lirias",
        "oai_url": "https://lirias.kuleuven.be/oai",
        "country": "BE",
        "lang_variant": "NL-BE",
    },
    {
        "name":    "vu_amsterdam",
        "label":   "VU Amsterdam Research Portal",
        "oai_url": "https://research.vu.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "utrecht",
        "label":   "Utrecht University Repository (DSpace)",
        "oai_url": "https://dspace.library.uu.nl/oai/request",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "groningen",
        "label":   "University of Groningen Pure",
        "oai_url": "https://pure.rug.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "maastricht",
        "label":   "Maastricht University CRIS",
        "oai_url": "https://cris.maastrichtuniversity.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "tu_delft",
        "label":   "TU Delft Institutional Repository",
        "oai_url": "https://repository.tudelft.nl/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "erasmus",
        "label":   "Erasmus University Rotterdam (Pure)",
        "oai_url": "https://pure.eur.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "radboud",
        "label":   "Radboud University Nijmegen Repository",
        "oai_url": "https://repository.ubn.ru.nl/oai/request",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "tilburg",
        "label":   "Tilburg University Pure",
        "oai_url": "https://pure.uvt.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "eindhoven",
        "label":   "Eindhoven University of Technology (Pure)",
        "oai_url": "https://pure.tue.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "wageningen",
        "label":   "Wageningen University & Research (e-depot)",
        "oai_url": "https://edepot.wur.nl/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "utwente",
        "label":   "University of Twente Research Portal",
        "oai_url": "https://research.utwente.nl/ws/oai",
        "country": "NL",
        "lang_variant": "NL",
    },
    {
        "name":    "ugent",
        "label":   "Ghent University Institutional Archive (biblio.ugent.be)",
        "oai_url": "https://biblio.ugent.be/oai",
        "country": "BE",
        "lang_variant": "NL-BE",
    },
    # South African Afrikaans universities (DSpace 7 — /server/oai/request)
    {
        "name":    "nwu",
        "label":   "North-West University DSpace",
        "oai_url": "https://repository.nwu.ac.za/server/oai/request",
        "country": "ZA",
        "lang_variant": "AF",
    },
    {
        "name":    "up",
        "label":   "University of Pretoria Repository",
        "oai_url": "https://repository.up.ac.za/server/oai/request",
        "country": "ZA",
        "lang_variant": "AF",
    },
    {
        "name":    "ufs",
        "label":   "University of the Free State Open Access",
        "oai_url": "https://scholar.ufs.ac.za/server/oai/request",
        "country": "ZA",
        "lang_variant": "AF",
    },
]


def _build_url(base: str, **kwargs: str) -> str:
    return f"{base}?{urllib.parse.urlencode(kwargs)}"


def _fetch_xml(url: str, timeout: int = 90, retries: int = 3) -> ET.Element | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return ET.fromstring(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 406, 503) and attempt == retries - 1:
                logger.warning("[university_repos_nl] HTTP %d: %s", exc.code, url[:120])
                return None
            time.sleep(3.0 * (attempt + 1))
        except Exception as exc:
            if attempt == retries - 1:
                logger.warning("[university_repos_nl] Failed: %s — %s", url[:120], exc)
                return None
            time.sleep(3.0 * (attempt + 1))
    return None


def _dc_values(record_el: ET.Element, tag: str) -> list[str]:
    results: list[str] = []
    for dc_container in record_el.iter():
        local = dc_container.tag.split("}")[-1] if "}" in dc_container.tag else dc_container.tag
        if local == "dc":
            for el in dc_container:
                el_local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
                if el_local == tag and el.text:
                    results.append(el.text.strip())
    if not results:
        for el in record_el.iter():
            local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if local == tag and el.text:
                results.append(el.text.strip())
    return results


def _record_id(record_el: ET.Element) -> str:
    header = record_el.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_el.find("header")
    if header is None:
        return ""
    ident = header.find(f"{{{_NS_OAI}}}identifier")
    if ident is None:
        ident = header.find("identifier")
    if ident is not None and ident.text:
        return ident.text.strip()
    return ""


def _is_deleted(record_el: ET.Element) -> bool:
    header = record_el.find(f"{{{_NS_OAI}}}header")
    if header is None:
        header = record_el.find("header")
    return header is not None and header.get("status") == "deleted"


def _is_dutch(languages: list[str]) -> bool:
    for lang in languages:
        l = lang.lower().strip()
        if l.startswith("nl") or l in ("dut", "nld", "dutch", "nederlands"):
            return True
        # Afrikaans — accepted for SA institutions
        if l.startswith("af") or l in ("afr", "afrikaans"):
            return True
    return False


_NC_ND_RE = re.compile(r'\bnc\b|\bnd\b|noncommercial|no.deriv', re.IGNORECASE)


def _is_allowed_license(rights_list: list[str]) -> bool:
    """Accept all records from OA repos unless explicitly NC/ND.

    Leiden/UvA/KU Leuven only expose open-access records via their OAI-PMH
    feeds. Many records have empty dc:rights — treat empty as allowed. Only
    reject records that explicitly signal NC or ND licensing.
    """
    for rights in rights_list:
        if _NC_ND_RE.search(rights):
            return False
    return True


def _extract_year(dates: list[str]) -> int | None:
    for d in dates:
        m = re.search(r'\b(19|20)\d{2}\b', d)
        if m:
            return int(m.group(0))
    return None


def _harvest_institution(inst: dict, output_dir: Path) -> int:
    """Harvest one institution via OAI-PMH. Returns number of new records saved."""
    inst_dir = output_dir / inst["name"]
    inst_dir.mkdir(parents=True, exist_ok=True)
    records_path = inst_dir / "records.jsonl"
    acq_date = datetime.date.today().isoformat()

    # Resume: load already-seen IDs
    seen_ids: set[str] = set()
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    seen_ids.add(json.loads(line)["record_id"])
                except (KeyError, json.JSONDecodeError):
                    pass
        if seen_ids:
            logger.info(
                "[university_repos_nl/%s] Resuming — %d records already saved",
                inst["name"], len(seen_ids),
            )

    new_count = 0
    page_num = 0
    url: str | None = _build_url(
        inst["oai_url"], verb="ListRecords", metadataPrefix="oai_dc"
    )

    dry_pages_since_hit = 0  # consecutive pages with 0 new Dutch records
    _MAX_DRY_PAGES = 300    # stop if 300 consecutive pages (30K records) yield nothing new
    # Yield-rate guard: if < 5 new Dutch records after 100 pages, this institution is done
    _YIELD_CHECK_PAGE = 100
    _YIELD_MIN = 5

    with records_path.open("a", encoding="utf-8") as fout:
        while url:
            page_num += 1
            page_new_count = 0
            logger.debug(
                "[university_repos_nl/%s] OAI page %d: %s",
                inst["name"], page_num, url[:100],
            )

            root = _fetch_xml(url)
            if root is None:
                logger.warning(
                    "[university_repos_nl/%s] Stopping — fetch failed on page %d",
                    inst["name"], page_num,
                )
                break

            # Check for OAI error
            for ns_pfx in (f"{{{_NS_OAI}}}", ""):
                err = root.find(f"{ns_pfx}error")
                if err is not None:
                    code = err.get("code", "")
                    if code == "noRecordsMatch":
                        url = None
                    else:
                        logger.warning(
                            "[university_repos_nl/%s] OAI error '%s': %s",
                            inst["name"], code, err.text or "",
                        )
                        url = None
                    break

            if url is None:
                break

            list_records = root.find(f"{{{_NS_OAI}}}ListRecords")
            if list_records is None:
                list_records = root.find("ListRecords")
            if list_records is None:
                break

            for record_el in list_records.findall(f"{{{_NS_OAI}}}record"):
                if _is_deleted(record_el):
                    continue
                rec_id = _record_id(record_el)
                if not rec_id or rec_id in seen_ids:
                    continue

                languages    = _dc_values(record_el, "language")
                rights_list  = _dc_values(record_el, "rights")

                # SA institutions (ZA) tag Afrikaans as "other" — skip lang check, rely on langdetect
                is_za = inst.get("country") == "ZA"
                if not is_za and not _is_dutch(languages):
                    continue
                if not _is_allowed_license(rights_list):
                    continue

                titles       = _dc_values(record_el, "title")
                descriptions = _dc_values(record_el, "description")
                identifiers  = _dc_values(record_el, "identifier")
                dates        = _dc_values(record_el, "date")
                creators     = _dc_values(record_el, "creator")

                year = _extract_year(dates)
                source_url = next(
                    (i for i in identifiers if i.startswith("http")), ""
                )

                record_obj = {
                    "institution":   inst["name"],
                    "lang_variant":  inst["lang_variant"],
                    "country":       inst["country"],
                    "record_id":     rec_id,
                    "titles":        titles,
                    "descriptions":  descriptions,
                    "rights":        rights_list,
                    "languages":     languages,
                    "identifiers":   identifiers,
                    "creators":      creators,
                    "year":          year,
                    "source_url":    source_url,
                    "acquisition_date": acq_date,
                }
                fout.write(json.dumps(record_obj, ensure_ascii=False) + "\n")
                seen_ids.add(rec_id)
                new_count += 1
                page_new_count += 1

            if page_num % 50 == 0:
                logger.info(
                    "[university_repos_nl/%s] Page %d — +%d Dutch records",
                    inst["name"], page_num, new_count,
                )

            # Low-yield early stopping: skip institutions that are mostly non-Dutch
            if page_new_count > 0:
                dry_pages_since_hit = 0
            else:
                dry_pages_since_hit += 1
                if dry_pages_since_hit >= _MAX_DRY_PAGES:
                    logger.warning(
                        "[university_repos_nl/%s] Stopping after %d consecutive dry pages"
                        " (only %d Dutch records found); moving to next institution.",
                        inst["name"], dry_pages_since_hit, new_count,
                    )
                    break

            # Yield-rate guard: stop if too few new records after minimum pages
            if page_num == _YIELD_CHECK_PAGE and new_count < _YIELD_MIN:
                logger.warning(
                    "[university_repos_nl/%s] Stopping after %d pages — only %d new Dutch"
                    " records (yield too low); institution likely already harvested.",
                    inst["name"], page_num, new_count,
                )
                break

            # Resumption token
            token_el = list_records.find(f"{{{_NS_OAI}}}resumptionToken")
            if token_el is None:
                token_el = list_records.find("resumptionToken")
            if token_el is not None and token_el.text and token_el.text.strip():
                url = _build_url(
                    inst["oai_url"],
                    verb="ListRecords",
                    resumptionToken=token_el.text.strip(),
                )
                time.sleep(_DELAY)
            else:
                url = None

    # Acquisition log
    entry = {
        "source":    SOURCE,
        "institution": inst["name"],
        "url":       inst["oai_url"],
        "license":   LICENSE,
        "acquisition_date": acq_date,
        "new_records": new_count,
    }
    with (output_dir / "acquisition_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(
        "[university_repos_nl/%s] Done — %d new Dutch records",
        inst["name"], new_count,
    )
    return new_count


def download(
    output_dir: Path,
    institutions: list[str] | None = None,
    dry_run: bool = False,
) -> None:
    """
    Harvest Dutch OAI-PMH records from Leiden, UvA, and KU Leuven.

    Args:
        output_dir:    Root output directory; one subdir per institution.
        institutions:  Optional list of institution names to harvest (default: all).
        dry_run:       Log what would be done without fetching.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        inst for inst in INSTITUTIONS
        if institutions is None or inst["name"] in institutions
    ]
    # Prioritize institutions not yet harvested (no records file on disk)
    def _has_records(inst: dict) -> bool:
        p = output_dir / inst["name"] / "records.jsonl"
        return p.exists() and p.stat().st_size > 0
    targets.sort(key=_has_records)  # False (new) before True (done)

    if dry_run:
        for inst in targets:
            logger.info(
                "[university_repos_nl] [dry-run] would harvest %s from %s",
                inst["label"], inst["oai_url"],
            )
        return

    total = 0
    for inst in targets:
        logger.info(
            "[university_repos_nl] === Harvesting %s (%s) ===",
            inst["label"], inst["oai_url"],
        )
        total += _harvest_institution(inst, output_dir)

    logger.info("[university_repos_nl] All done — %d total new Dutch records", total)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Harvest Dutch academic records from Leiden/UvA/KU Leuven OAI-PMH"
    )
    parser.add_argument("--output",       required=True, type=Path)
    parser.add_argument("--institutions", nargs="*",
                        help="leiden / uva / kuleuven (default: all)")
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args()
    download(args.output, institutions=args.institutions, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
