"""
Lataa Finlex statute-consolidated ZIP:n ja parsii sen finlex_parser.py:llä.
Tuottaa data/consolidated_all.csv samassa muodossa kuin build_consolidated.py.

Käyttö:
    python parser/build_from_zip.py               # lataa + parsii
    python parser/build_from_zip.py --parse-only  # parsii jo ladatusta ZIP:stä

ZIP:    parser/input/statute-consolidated.zip  (~4 GB)
Output: data/consolidated_all.csv
"""
import argparse
import csv
import io
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finlex_parser import (
    _extract_law_id, _extract_law_title, _is_in_force, _extract_version,
    _walk, AKN_NS
)

# ── Vakiot: ZIP:n latauspaikka, tallennuspolku ja CSV:n sarakkeet ─────────────
ZIP_URL  = "https://www.finlex.fi/api/assets/open-data/archives/statute-consolidated.zip"
ZIP_PATH = Path(__file__).resolve().parent / "input" / "statute-consolidated.zip"
OUT_CSV  = Path(__file__).resolve().parents[1] / "data" / "consolidated_all.csv"

VERSION_RE = re.compile(r"^(.*)/fin@([^/]*)/[^/]+\.xml$")

COLUMNS = [
    "law_id", "law_title", "isInForce", "version", "source_file",
    "eId", "type", "num", "heading", "intro", "content",
    "alakohdat", "hash", "parent_eId", "depth",
    "has_ref", "refs", "parser_mode",
]


# ── ZIP:n lataus: hakee Finlexin avoimen datan arkiston verkosta ja tallentaa levylle ─
def download_zip():
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Ladataan: {ZIP_URL}")
    print(f"Kohde:    {ZIP_PATH}  (~4 GB)")
    with requests.get(ZIP_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done  = 0
        with open(ZIP_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done/1024/1024:.0f} / {total/1024/1024:.0f} MB ({done/total*100:.1f}%)",
                          end="", flush=True)
    print(f"\nLadattu.")


# ── Versioiden valinta: valitsee ZIP:stä uusimman suomenkielisen (fin@) version kustakin laista ─
def pick_latest_finnish(zf: zipfile.ZipFile) -> dict[str, str]:
    """Palauttaa {laki_avain: zip_polku} — viimeisin fin@-versio per laki."""
    candidates = defaultdict(list)
    for name in zf.namelist():
        if "fin@" not in name or not name.endswith(".xml"):
            continue
        m = VERSION_RE.match(name)
        if not m:
            continue
        candidates[m.group(1)].append((m.group(2), name))
    return {k: sorted(v)[-1][1] for k, v in candidates.items()}


# ── XML:n parsiminen: muuntaa yhden lain XML-tavut dict-rivien listaksi käyttäen finlex_parser._walk() ─
def parse_to_rows(xml_bytes: bytes, source_name: str) -> list[dict]:
    """Parsii XML-tavut ja palauttaa listana dict-rivejä (ei DataFramea)."""
    try:
        root = etree.fromstring(xml_bytes, etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        return []

    if _is_in_force(root) != "true":
        return []

    law_id    = _extract_law_id(root)
    law_title = _extract_law_title(root)
    is_force  = _is_in_force(root)
    version   = _extract_version(root)

    base = {
        "law_id":      law_id,
        "law_title":   law_title,
        "isInForce":   is_force,
        "version":     version,
        "source_file": source_name,
    }

    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        return []

    rows = []
    for child in body:
        _walk(child, base, law_id, 1, rows)

    for r in rows:
        r["parser_mode"] = "main"

    return rows


# ── Pääohjelma: lataa ZIP (tai käyttää olemassa olevaa), parsii kaikki lait ja kirjoittaa CSV:n ─
def main(parse_only: bool = False):
    if not parse_only:
        download_zip()
    elif not ZIP_PATH.exists():
        raise SystemExit(f"ZIP ei löydy: {ZIP_PATH}")

    print(f"\nSkannataan ZIP-hakemisto...")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    skipped    = 0

    with zipfile.ZipFile(ZIP_PATH) as zf, \
         open(OUT_CSV, "w", encoding="utf-8", newline="") as out_f:

        writer = csv.DictWriter(out_f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()

        latest = pick_latest_finnish(zf)
        total  = len(latest)
        print(f"Uniikkeja suomenkielisiä lakeja: {total:,}")
        print(f"Parsitaan (vain isInForce=true)...\n")

        for i, (law_key, zip_path) in enumerate(latest.items(), 1):
            xml_bytes = zf.read(zip_path)
            rows = parse_to_rows(xml_bytes, Path(zip_path).name)

            if not rows:
                skipped += 1
            else:
                writer.writerows(rows)
                total_rows += len(rows)

            if i % 5000 == 0:
                print(f"  {i:,}/{total:,} — rivejä: {total_rows:,} — ohitettu: {skipped:,}")

    print(f"\nValmis!")
    print(f"Lakeja (isInForce): {total - skipped:,}  ohitettu: {skipped:,}")
    print(f"Rivejä yhteensä:    {total_rows:,}")
    print(f"Tallennettu:        {OUT_CSV}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--parse-only", action="store_true")
    args = ap.parse_args()
    main(parse_only=args.parse_only)
