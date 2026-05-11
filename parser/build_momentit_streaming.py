"""
Streaming-pipeline: ZIP → momenttitason DataFrame → CSV.

Ei kirjoita välivaihetta levyll (ei consolidated_all.csv, ei
consolidated_sections.csv). Kaikki vaiheet muistissa polars
DataFrame -rakenteena.

Tuottaa yhden rivin per momentti (subsection). Mikäli pykälällä ei
ole momentteja erikseen, koko pykälän teksti tulee yhdelle riville.

Kollegan palautteen mukaisesti tuotettu erilliset sarakkeet:
- statute_type    (laki / asetus / päätös)
- ministry        (vastaava ministeriö, jos tunnistettavissa XML:stä)
- part_num, part_heading
- chapter_num, chapter_heading
- section_num, section_heading
- section_intro   (pykälän johdantokappale)
- section_wrapup  (pykälän loppukappale — esim. rangaistussäännöksissä)
- subsection_num, subsection_text, subsection_alakohdat
- modaliteetti        (regex yksiluokka, V5)
- modaliteetti_set    (regex multi-label, putkella eroteltu)

Aja:
    python parser/build_momentit_streaming.py
"""
from __future__ import annotations
import io
import os
import re
import sys
import time
import zipfile

# Windowsin oletuskonsoli on cp1252 — pakota UTF-8 jotta erikoismerkit toimivat
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
from collections import defaultdict
from pathlib import Path

import polars as pl
from lxml import etree

# Käytä olemassa olevia luokittimia
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deontic_classifier import classify
from deontic_classifier_multilabel import classify_multilabel

# ── Polut ─────────────────────────────────────────────────────────────────────

ROOT     = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "parser" / "input" / "statute-consolidated.zip"
OUT_CSV  = ROOT / "data" / "asiakas_jakelu" / "momentit.csv"

# ── XML-nimiavaruudet ─────────────────────────────────────────────────────────

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
FNX_NS = "http://data.finlex.fi/schema/finlex"
NS = {"akn": AKN_NS, "fnx": FNX_NS}

# Matchaa ZIP-polkuja muotoa "akn/fi/act/statute-consolidated/1961/142/fin@/main.xml"
# Group 1 = lakitunniste (kaikki ennen /fin@), Group 2 = versiopvm (mahd. tyhjä)
VERSION_RE = re.compile(r"^(.*)/fin@([^/]*)/[^/]+\.xml$")


# ── Apufunktiot XML:n käsittelyyn ────────────────────────────────────────────

def localname(el) -> str:
    return etree.QName(el.tag).localname


def all_text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def child_text(parent, name: str) -> str:
    if parent is None:
        return ""
    for ch in parent:
        if localname(ch) == name:
            return all_text(ch)
    return ""


# ── ZIPin lakikohtainen versiointi ────────────────────────────────────────────

def pick_latest(zf: zipfile.ZipFile) -> dict[str, str]:
    """Valitsee ZIPistä per laki uusimman suomenkielisen version (fin@-päivä).
    Vastaa build_from_zip.py:n pick_latest_finnish()-funktiota."""
    candidates: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name in zf.namelist():
        if "fin@" not in name or not name.endswith(".xml"):
            continue
        m = VERSION_RE.match(name)
        if not m:
            continue
        candidates[m.group(1)].append((m.group(2), name))
    return {k: sorted(v)[-1][1] for k, v in candidates.items()}


# ── Metatietojen poiminta lain tasolta ───────────────────────────────────────

STATUTE_TYPE_MAP = {
    "act":        "laki",
    "decree":     "asetus",
    "decision":   "päätös",
    "regulation": "määräys",
    "guideline":  "ohje",
}


def extract_meta(root) -> dict:
    """Poimii lain tason metatiedot: tunniste, otsikko, tyyppi, ministeriö,
    voimassaolo, versionumero."""
    # law_id (esim. "729_2018")
    val = root.xpath("string(//*[local-name()='FRBRWork']//*[local-name()='FRBRthis'][1]/@value)")
    m = re.search(r"/akn/fi/act/[^/]+/(\d{4})/([^/]+)", val or "")
    law_id = f"{m.group(2)}_{m.group(1)}" if m else ""

    # title: oikea otsikko on preface/p/docTitle (esim. "Tietosuojalaki")
    # — FRBRalias palauttaa URLin, ei käytetä.
    title = ""
    for xp in (
        ".//akn:preface/akn:p/akn:docTitle",
        ".//akn:preface/akn:docTitle",
        ".//akn:preface/akn:p/akn:shortTitle",
        ".//akn:preface/akn:longTitle",
    ):
        el = root.find(xp, NS)
        if el is not None:
            title = "".join(el.itertext()).strip()
            if title:
                break
    title = re.sub(r"\s+", " ", title or "").strip().rstrip(".")

    # isInForce
    el = root.find(".//akn:meta/akn:proprietary/fnx:isInForce", NS)
    is_force = el.get("value", "") if el is not None else ""

    # version
    el = root.find(".//akn:FRBRExpression/akn:FRBRversionNumber", NS)
    version = el.get("value", "") if el is not None else ""

    # statute_type
    el = root.find(".//akn:meta/akn:proprietary/fnx:typeStatute", NS)
    type_ref = (el.get("refersTo", "") if el is not None else "").lstrip("#")
    statute_type = STATUTE_TYPE_MAP.get(type_ref.lower(), type_ref)

    # ministry: vastaava ministeriö löytyy proprietary-lohkon
    # <finlex:administrativeBranch refersTo="#xxxx"/> -elementistä,
    # ja varsinainen nimi <TLCOrganization eId="xxxx" showAs="..."/>:sta.
    ministry = ""
    ab = root.find(".//akn:meta/akn:proprietary/fnx:administrativeBranch", NS)
    if ab is not None:
        ref = (ab.get("refersTo", "") or "").lstrip("#")
        if ref:
            org = root.find(f".//akn:references/akn:TLCOrganization[@eId='{ref}']", NS)
            if org is not None:
                ministry = org.get("showAs", "")
            else:
                # Fallback: käytä refersTo-tunnistetta sellaisenaan
                ministry = ref

    return {
        "law_id":       law_id,
        "law_title":    title,
        "statute_type": statute_type,
        "ministry":     ministry,
        "isInForce":    is_force,
        "version":      version,
    }


# ── Rivien generointi momenttitasolle ────────────────────────────────────────

def walk(el, ctx: dict, base: dict, rows: list):
    """Rekursiivinen läpikäynti. Lopulliset rivit syntyvät subsection-tasolla
    (tai section-tasolla, jos section ei sisällä subsection-elementtejä)."""
    name = localname(el)

    if name == "part":
        new_ctx = {**ctx,
                   "part_eId":     el.get("eId", ""),
                   "part_num":     child_text(el, "num"),
                   "part_heading": child_text(el, "heading")}
        for ch in el:
            walk(ch, new_ctx, base, rows)
        return

    if name == "chapter":
        new_ctx = {**ctx,
                   "chapter_eId":     el.get("eId", ""),
                   "chapter_num":     child_text(el, "num"),
                   "chapter_heading": child_text(el, "heading")}
        for ch in el:
            walk(ch, new_ctx, base, rows)
        return

    if name == "section":
        # Poimi section-tason intro ja wrapup
        sec_intro = ""
        sec_wrapup = ""
        for ch in el:
            cn = localname(ch)
            if cn == "intro":
                sec_intro = all_text(ch)
            elif cn in ("wrapUp", "wrapup"):
                sec_wrapup = all_text(ch)

        new_ctx = {**ctx,
                   "section_eId":     el.get("eId", ""),
                   "section_num":     child_text(el, "num"),
                   "section_heading": child_text(el, "heading"),
                   "section_intro":   sec_intro,
                   "section_wrapup":  sec_wrapup}

        subs = [c for c in el if localname(c) == "subsection"]
        if subs:
            for sub in subs:
                walk(sub, new_ctx, base, rows)
        else:
            # Pykälällä ei ole momenttijaottelua → koko pykälä yhdelle riville
            text_parts = []
            alakohdat = []
            for ch in el:
                cn = localname(ch)
                if cn == "list":
                    for li in ch:
                        if localname(li) == "point":
                            alakohdat.append(all_text(li))
                elif cn not in {"num", "heading", "intro", "wrapUp", "wrapup",
                                "subsection", "paragraph"}:
                    text_parts.append(all_text(ch))
            own = " ".join(p for p in text_parts if p).strip()

            row = {**base, **new_ctx,
                   "subsection_eId":       "",
                   "subsection_num":       "",
                   "subsection_text":      own,
                   "subsection_alakohdat": " | ".join(alakohdat),
                   "n_alakohdat":          len(alakohdat)}
            rows.append(row)
        return

    if name == "subsection":
        text_parts = []
        alakohdat = []
        for ch in el:
            cn = localname(ch)
            if cn == "list":
                for li in ch:
                    if localname(li) == "point":
                        alakohdat.append(all_text(li))
            elif cn not in {"num", "heading", "intro", "wrapUp", "wrapup"}:
                text_parts.append(all_text(ch))
        own = " ".join(p for p in text_parts if p).strip()

        row = {**base, **ctx,
               "subsection_eId":       el.get("eId", ""),
               "subsection_num":       child_text(el, "num"),
               "subsection_text":      own,
               "subsection_alakohdat": " | ".join(alakohdat),
               "n_alakohdat":          len(alakohdat)}
        rows.append(row)
        return

    # Muut elementit: rekursioidaan eteenpäin (esim. hcontainer)
    for ch in el:
        walk(ch, ctx, base, rows)


def extract_rows(root, base_meta: dict) -> list[dict]:
    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        return []
    rows: list[dict] = []
    initial_ctx = {
        "part_eId": "",     "part_num": "",     "part_heading": "",
        "chapter_eId": "",  "chapter_num": "",  "chapter_heading": "",
        "section_eId": "",  "section_num": "",  "section_heading": "",
        "section_intro": "", "section_wrapup": "",
    }
    walk(body, initial_ctx, base_meta, rows)
    return rows


# ── Streaming-pääfunktio ──────────────────────────────────────────────────────

def build_dataframe() -> pl.DataFrame:
    print(f"Avataan: {ZIP_PATH}")
    rows: list[dict] = []
    t0 = time.time()
    n_kept = n_skipped = 0

    with zipfile.ZipFile(ZIP_PATH) as zf:
        latest = pick_latest(zf)
        print(f"  Uniikkeja lakeja ZIPissä: {len(latest):,}")

        for i, (law_key, zip_path) in enumerate(latest.items(), 1):
            try:
                xml_bytes = zf.read(zip_path)
                root = etree.fromstring(xml_bytes, etree.XMLParser(recover=True))
            except Exception:
                n_skipped += 1
                continue

            # Pick_latest valitsee uusimman version per säädös → kohdellaan
            # viimeisintä versiota voimassa olevana. isInForce-kenttä otetaan
            # mukaan tietona mutta sitä ei käytetä suodatukseen.
            base_meta = extract_meta(root)
            base_meta["source_file"] = Path(zip_path).name
            statute_rows = extract_rows(root, base_meta)
            if statute_rows:
                rows.extend(statute_rows)
            else:
                # contentAbsent / muu rakenne → tuota yksi metarivi
                rows.append({
                    **base_meta,
                    "part_eId": "", "part_num": "", "part_heading": "",
                    "chapter_eId": "", "chapter_num": "", "chapter_heading": "",
                    "section_eId": "", "section_num": "", "section_heading": "",
                    "section_intro": "", "section_wrapup": "",
                    "subsection_eId": "", "subsection_num": "",
                    "subsection_text": "", "subsection_alakohdat": "",
                    "n_alakohdat": 0,
                })
            n_kept += 1

            if i % 5000 == 0:
                print(f"  {i:,}/{len(latest):,}  rivit: {len(rows):,}  "
                      f"({time.time()-t0:.0f}s)", flush=True)

    print(f"\nParsittu: {n_kept:,} voimassa olevaa säädöstä "
          f"→ {len(rows):,} momenttitason riviä")
    print(f"Suodatettu pois: {n_skipped:,}")

    if not rows:
        return pl.DataFrame()

    df = pl.DataFrame(rows)
    return df


# ── Regex-luokittelu DataFrameen ─────────────────────────────────────────────

def add_classifications(df: pl.DataFrame) -> pl.DataFrame:
    print(f"Lasketaan modaliteetit {df.height:,} riville...")
    # Luokitellaan koko momentin sisältö: section_intro + subsection_text + alakohdat
    df = df.with_columns(
        (pl.col("section_intro").fill_null("") + " " +
         pl.col("subsection_text").fill_null("") + " " +
         pl.col("subsection_alakohdat").fill_null("")
        ).str.strip_chars().alias("_text_for_classify")
    )
    texts = df["_text_for_classify"].to_list()

    t0 = time.time()
    modaliteetti     = [classify(t) for t in texts]
    modaliteetti_set = ["|".join(sorted(classify_multilabel(t))) for t in texts]
    print(f"  Aika: {time.time()-t0:.1f}s")

    return df.with_columns([
        pl.Series("modaliteetti",     modaliteetti),
        pl.Series("modaliteetti_set", modaliteetti_set),
    ]).drop("_text_for_classify")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    df = build_dataframe()
    if df.is_empty():
        print("Tyhjä DataFrame — ei mitään kirjoitettavaa.")
        return

    df = add_classifications(df)

    # Järjestä sarakkeet luonnolliseen järjestykseen
    desired_order = [
        "law_id", "law_title", "statute_type", "ministry",
        "isInForce", "version", "source_file",
        "part_eId", "part_num", "part_heading",
        "chapter_eId", "chapter_num", "chapter_heading",
        "section_eId", "section_num", "section_heading",
        "section_intro", "section_wrapup",
        "subsection_eId", "subsection_num", "subsection_text",
        "subsection_alakohdat", "n_alakohdat",
        "modaliteetti", "modaliteetti_set",
    ]
    cols = [c for c in desired_order if c in df.columns] + \
           [c for c in df.columns if c not in desired_order]
    df = df.select(cols)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(str(OUT_CSV))

    sz_mb = os.path.getsize(OUT_CSV) / 1024 / 1024
    print(f"\nKirjoitettu: {OUT_CSV}")
    print(f"  Koko:      {sz_mb:.1f} MB")
    print(f"  Rivit:     {df.height:,}")
    print(f"  Sarakkeet: {df.width}")
    print()
    print("=== Modaliteettijakauma (yksiluokka) ===")
    print(df.group_by("modaliteetti").len().sort("len", descending=True))
    print()
    print("=== Säädöstyyppi-jakauma ===")
    print(df.group_by("statute_type").len().sort("len", descending=True).head(10))
    print()
    print("=== Top ministeriöt ===")
    print(df.filter(pl.col("ministry") != "").group_by("ministry").len()
          .sort("len", descending=True).head(10))


if __name__ == "__main__":
    main()
