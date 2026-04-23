"""
Aggregoi consolidated_all.csv pykälätasolle.
Jokainen section-rivi saa kaikkien lapsirakenteidensa tekstit yhdistettynä.
Luokittelee samalla deontic_classifier.py:lla.

Kaytto:
    python parser/build_sections.py

Input:  data/consolidated_all.csv
Output: data/consolidated_sections.csv  (yksi rivi per pykala)
"""
import csv
import io
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deontic_classifier import classify

IN_CSV  = Path(__file__).resolve().parents[1] / "data" / "consolidated_all.csv"
OUT_CSV = Path(__file__).resolve().parents[1] / "data" / "consolidated_sections.csv"

COLUMNS = [
    "law_id", "law_title", "isInForce", "version",
    "eId", "num", "heading",
    "text",       # kaikki lapsirakenteiden intro+content yhdistattyina
    "alakohdat",  # kaikki alakohdat pipe-erotettuina
    "n_elements", # kuinka monta XML-elementtia aggregoitiin
    "modaliteetti",
]

# Elementtityypit jotka kelpaavat pykalan juureksi jos section-riveja ei ole
SECTION_TYPES = {"section"}
FALLBACK_TYPES = {"chapter", "part", "annex", "hcontainer"}

print("Luetaan CSV...", flush=True)
t0 = time.time()

by_law: dict[str, list[dict]] = defaultdict(list)
total_in = 0
with open(IN_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        by_law[row["law_id"]].append(row)
        total_in += 1

print(f"  {total_in:,} riviä, {len(by_law):,} lakia — {time.time()-t0:.1f}s", flush=True)
print("Aggregoidaan pykälät...", flush=True)

output_rows: list[dict] = []

for law_id, rows in by_law.items():
    # Valitaan juuritaso: section ensin, muuten fallback-tyypit
    roots = [r for r in rows if r["type"] in SECTION_TYPES]
    if not roots:
        roots = [r for r in rows if r["type"] in FALLBACK_TYPES]
    if not roots:
        # Laki ilman rakennetta — kootaan kaikki teksti yhteen riviin
        texts = [" ".join(filter(None, [r.get("intro",""), r.get("content","")])) for r in rows]
        full  = " ".join(filter(None, texts)).strip()
        if full:
            output_rows.append({
                "law_id":      law_id,
                "law_title":   rows[0]["law_title"],
                "isInForce":   rows[0]["isInForce"],
                "version":     rows[0]["version"],
                "eId":         "",
                "num":         "",
                "heading":     "",
                "text":        full,
                "alakohdat":   "",
                "n_elements":  len(rows),
                "modaliteetti": classify(full),
            })
        continue

    for root in roots:
        eid = root.get("eId", "")

        # Keraa kaikki jalkelaiset eId-prefiksin perusteella
        if eid:
            descendants = [r for r in rows if r.get("eId") == eid
                           or r.get("eId", "").startswith(eid + "__")]
        else:
            descendants = [root]

        # Jarjesta hierarkiajärjestykseen (depth nouseva)
        descendants.sort(key=lambda r: int(r.get("depth") or 0))

        # Aggregoi teksti: intro ja content kaikista tasoista
        text_parts   = []
        kohdat_parts = []
        for d in descendants:
            if d.get("intro"):
                text_parts.append(d["intro"])
            if d.get("content"):
                text_parts.append(d["content"])
            if d.get("alakohdat"):
                kohdat_parts.append(d["alakohdat"])

        full_text = " ".join(filter(None, text_parts)).strip()
        alakohdat  = " | ".join(filter(None, kohdat_parts))

        output_rows.append({
            "law_id":      root["law_id"],
            "law_title":   root["law_title"],
            "isInForce":   root["isInForce"],
            "version":     root["version"],
            "eId":         eid,
            "num":         root.get("num", ""),
            "heading":     root.get("heading", ""),
            "text":        full_text,
            "alakohdat":   alakohdat,
            "n_elements":  len(descendants),
            "modaliteetti": classify(full_text),
        })

print(f"  {len(output_rows):,} pykälää — {time.time()-t0:.1f}s", flush=True)
print("Kirjoitetaan CSV...", flush=True)

with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(output_rows)

elapsed = time.time() - t0
print(f"\nValmis: {len(output_rows):,} pykälää — {elapsed:.1f}s")
print(f"Tallennettu: {OUT_CSV}")
