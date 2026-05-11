"""
Generoi asiakas_jakelun johdannaiset suoraan momentit.csv:stä:

  1. consolidated_sections_lite.csv  — yksi rivi per pykälä,
                                       sisältää pykälätason modaliteetin
  2. regex_propositions.csv          — (toimija, modaliteetti, kohde)-triplet
                                       regex-pohjaisesti pykälätason tekstistä

Ei käytä välivaiheen consolidated_sections.csv:tä — kaikki ajetaan
momentit.csv:n päältä polars-aggregaatiolla.

Aja:
    python parser/build_jakelu_from_momentit.py
"""
from __future__ import annotations
import io
import sys
import csv
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deontic_classifier import classify
from proposition_extractor import extract_propositions

ROOT = Path(__file__).resolve().parents[1]
IN_MOMENTIT = ROOT / "data" / "asiakas_jakelu" / "momentit.csv"
OUT_LITE    = ROOT / "data" / "asiakas_jakelu" / "consolidated_sections_lite.csv"
OUT_PROPS   = ROOT / "data" / "asiakas_jakelu" / "regex_propositions.csv"


def aggregate_to_sections(df: pl.DataFrame) -> pl.DataFrame:
    """Yhdistä momenttirivit pykälätasolle: yksi rivi per (law_id, section_eId)."""
    # Yhdistä section_intro + subsection_text + subsection_alakohdat → pykälän koko teksti
    df = df.with_columns(
        (
            pl.col("section_intro").fill_null("") + " " +
            pl.col("subsection_text").fill_null("") + " " +
            pl.col("subsection_alakohdat").fill_null("")
        ).str.strip_chars().alias("_momentti_text")
    )
    # Aggregoi pykälätasolle
    g = (
        df.group_by(["law_id", "law_title", "section_eId"], maintain_order=True)
          .agg([
              pl.col("section_num").first().alias("num"),
              pl.col("section_heading").first().alias("heading"),
              pl.col("_momentti_text").str.concat(" ").alias("text"),
          ])
    )
    return g


def build_lite_csv(sections: pl.DataFrame) -> None:
    print("Lasketaan pykälätason modaliteetti...")
    texts = sections["text"].to_list()
    t0 = time.time()
    modaliteetti = [classify(t) for t in texts]
    print(f"  Aika: {time.time()-t0:.1f}s ({len(texts):,} pykälää)")

    lite = sections.with_columns(
        pl.Series("modaliteetti_v4", modaliteetti)
    ).select([
        pl.col("law_id"),
        pl.col("law_title"),
        pl.col("section_eId").alias("eId"),
        pl.col("num"),
        pl.col("heading"),
        pl.col("modaliteetti_v4"),
    ])
    OUT_LITE.parent.mkdir(parents=True, exist_ok=True)
    lite.write_csv(str(OUT_LITE))
    print(f"Kirjoitettu: {OUT_LITE}  ({lite.height:,} riviä)")


def build_propositions_csv(sections: pl.DataFrame) -> None:
    print("Aja propositio-ekstraktori...")
    t0 = time.time()
    n_in = n_out = 0
    OUT_PROPS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PROPS, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["law_id", "law_title", "eId", "num",
                    "toimija", "modaliteetti", "kohde", "distance"])
        for row in sections.iter_rows(named=True):
            n_in += 1
            text = row.get("text") or ""
            if len(text) < 20:
                continue
            for t in extract_propositions(text):
                w.writerow([
                    row["law_id"], row["law_title"], row["section_eId"],
                    row["num"], t.toimija, t.modaliteetti, t.kohde, t.distance,
                ])
                n_out += 1
            if n_in % 10000 == 0:
                print(f"  {n_in:,} pykälää, {n_out:,} propositiota ({time.time()-t0:.0f}s)", flush=True)
    print(f"Valmis: {n_in:,} pykälää -> {n_out:,} propositiota")
    print(f"Kirjoitettu: {OUT_PROPS}")


def main():
    print(f"Lue {IN_MOMENTIT}")
    df = pl.read_csv(str(IN_MOMENTIT), infer_schema_length=10000)
    print(f"  Rivejä: {df.height:,}")

    # Jätä pois pelkät metarivit (contentAbsent) joilla section_eId on tyhjä
    df = df.filter(pl.col("section_eId") != "")
    print(f"  Rivejä joilla section_eId: {df.height:,}")

    sections = aggregate_to_sections(df)
    print(f"  Aggregoitu {sections.height:,} pykälään")

    build_lite_csv(sections)
    build_propositions_csv(sections)


if __name__ == "__main__":
    main()
