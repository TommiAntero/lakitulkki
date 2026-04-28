"""
Lisaa otokseen rikos-, vero-, yksityis-, yritys- ym. erityislakeja jotka eivat ole
mukana THK-pohjaisessa otoksessa. Kayttaa consolidated_sections.csv:ta (rivi per
pykala, kaikki lapsielementit yhdistettyna).

Tallennetaan samaan tiedostoon kuin THK-otos (deontic_thk_sample.csv) niin etta
raportti loytaa kaikki rivit. Uudet rivit saavat org_tyyppi-arvon kategorian
mukaan: RIKOS / VERO / YKSITYIS / YRITYS / TYO / HALLINTO / ERIKOIS.

Resume-logiikka: ohittaa rivit jotka loytyvat jo deontic_thk_sample.csv:sta
tunnisteparilla (law_id, eId, num).

Kaytto:
    python -u parser/annotate_extra.py
"""
import csv
import os
import sys
import time
from pathlib import Path

env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import anthropic
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deontic_prompt import SYSTEM_PROMPT, build_user_prompt, parse_response

# ── Laki-lista kategorioittain ────────────────────────────────────────────────

LAWS: dict[str, list[tuple[str, str]]] = {
    "RIKOS": [
        ("39-001_1889", "Rikoslaki"),
        ("1_1998",      "Ampuma-aselaki"),
        ("806_2011",    "Pakkokeinolaki"),
        ("805_2011",    "Esitutkintalaki"),
        ("767_2005",    "Vankeuslaki"),
        ("768_2005",    "Tutkintavankeuslaki"),
        ("373_2008",    "Huumausainelaki"),
        ("612_2003",    "Jarjestyslaki"),
        ("689_1997",    "Laki oikeudenkaynnista rikosasioissa"),
    ],
    "VERO": [
        ("1535_1992",   "Tuloverolaki"),
        ("1501_1993",   "Arvonlisaverolaki"),
        ("378_1940",    "Perinto- ja lahjaverolaki"),
        ("1558_1995",   "Verotusmenettelylaki"),
        ("777_2020",    "Autoverolaki"),
    ],
    "YKSITYIS": [
        ("234_1929",    "Avioliittolaki"),
        ("40_1965",     "Perintokaari"),
        ("540_1995",    "Maakaari"),
        ("543_1994",    "Vakuutussopimuslaki"),
        ("442_1999",    "Holhoustoimilaki"),
        ("843_1994",    "Asuntokauppalaki"),
        ("38_1978",     "Kuluttajansuojalaki"),
    ],
    "YRITYS": [
        ("624_2006",    "Osakeyhtiolaki"),
        ("1599_2009",   "Asunto-osakeyhtiolaki"),
        ("120_2004",    "Konkurssilaki"),
        ("47_1993",     "Yrityssaneerauslaki"),
        ("57_1993",     "Velkajarjestelylaki"),
        ("1336_1997",   "Kirjanpitolaki"),
        ("421_2013",    "Osuuskuntalaki"),
        ("521_2008",    "Vakuutusyhtiolaki"),
        ("610_2014",    "Laki luottolaitostoiminnasta"),
    ],
    "TYO": [
        ("55_2001",     "Tyosopimuslaki"),
        ("738_2002",    "Tyoturvallisuuslaki"),
        ("459_2015",    "Tyotapaturma- ja ammattitautilaki"),
    ],
    "HALLINTO": [
        ("434_2003",    "Hallintolaki"),
        ("359_2003",    "Kansalaisuuslaki"),
        ("4-000_1734",  "Oikeudenkaymiskaari"),
        ("808_2019",    "Laki oikeudenkaynnista hallintoasioissa"),
    ],
    "ERIKOIS": [
        ("404_1961",    "Tekijanoikeuslaki"),
        ("550_1967",    "Patenttilaki"),
        ("1047_2001",   "Arpajaislaki"),
        ("386_2011",    "Ajokorttilaki"),
        ("693_2023",    "Eläinten hyvinvointi"),
        ("379_2015",    "Kalastuslaki"),
        ("390_2005",    "Vaarallisten kemikaalien turvallisuus"),
        ("1135_2016",   "Sähköturvallisuuslaki"),
        ("782_2019",    "Vesiliikennelaki"),
    ],
}

# ── Asetukset ─────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).resolve().parents[1]
IN_CSV     = ROOT / "data" / "consolidated_sections.csv"
OUT_CSV    = ROOT / "data" / "deontic_thk_sample.csv"  # sama tiedosto kuin THK
MODEL      = "claude-haiku-4-5-20251001"
SAVE_EVERY = 200

# ── Kerätään law_id -> kategoria mappaus ──────────────────────────────────────

law_to_cat: dict[str, str] = {}
for cat, laws in LAWS.items():
    for lid, _name in laws:
        law_to_cat[lid] = cat

print(f"Lakeja yhteensa: {len(law_to_cat)}")
for cat, laws in LAWS.items():
    print(f"  {cat:10s} {len(laws):3d} lakia")

# ── Lue ja suodata aineisto ──────────────────────────────────────────────────

print("\nLuetaan consolidated_sections.csv...")
df = pl.read_csv(str(IN_CSV), infer_schema_length=0)

target = (df.filter(pl.col("law_id").is_in(list(law_to_cat.keys())))
            .filter(pl.col("text").str.len_chars() > 20))

print(f"Pykalia naissa laeissa: {target.height:,}")
for cat in LAWS:
    ids = [lid for lid, _ in LAWS[cat]]
    n = target.filter(pl.col("law_id").is_in(ids)).height
    print(f"  {cat:10s} n={n:5,}")

# Lisätään org_tyyppi-sarake kategorian mukaan
target = target.with_columns(
    pl.col("law_id").map_elements(lambda x: law_to_cat.get(x, ""), return_dtype=pl.String).alias("org_tyyppi")
)

# ── Resume: lue jo käsitellyt rivit ──────────────────────────────────────────

def row_key(row):
    return (row.get("law_id", ""), row.get("eId", ""), row.get("num", ""))

already_done: set[tuple] = set()
if OUT_CSV.exists():
    with open(OUT_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            already_done.add(row_key(r))
    print(f"\nResume: {len(already_done):,} riviä jo tehty (THK-otos + mahd. aiempi extra-ajo)")

rows_to_do = [r for r in target.iter_rows(named=True) if row_key(r) not in already_done]
total      = len(rows_to_do)
appending  = bool(already_done)

print(f"\nAnnotoidaan: {total:,} uutta riviä  (mallilla {MODEL})")
print(f"Tallennetaan valitulos joka {SAVE_EVERY} rivin valein.\n")

# ── Sarakelista — sama kuin THK-otoksessa ─────────────────────────────────────

if OUT_CSV.exists():
    with open(OUT_CSV, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        COLS = next(reader)
else:
    COLS = list(target.columns) + ["modaliteetti", "oikeussubjekti", "perustelu"]

def flush(rows, path, append):
    if not rows:
        return
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        if not append:
            w.writeheader()
        w.writerows(rows)

# ── Claude API ────────────────────────────────────────────────────────────────

client = anthropic.Anthropic(max_retries=0)
results = []
errors  = 0
done_start = len(already_done)

for i, row in enumerate(rows_to_do, 1):
    text = (row.get("text") or "").strip()
    if not text:
        continue

    user_prompt = build_user_prompt(
        text      = text[:3000],
        law_title = row.get("law_title", ""),
        section   = row.get("num") or row.get("eId") or "",
    )

    try:
        response = client.messages.create(
            model      = MODEL,
            max_tokens = 256,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_prompt}],
            timeout    = 30.0,
        )
        raw    = response.content[0].text
        parsed = parse_response(raw)
    except Exception as e:
        errors += 1
        parsed = {"modaliteetti": "virhe", "oikeussubjekti": "", "perustelu": str(e)[:100]}

    results.append({
        **row,
        "modaliteetti":   parsed.get("modaliteetti", ""),
        "oikeussubjekti": parsed.get("oikeussubjekti", ""),
        "perustelu":      parsed.get("perustelu", ""),
    })

    if len(results) >= SAVE_EVERY:
        flush(results, OUT_CSV, appending)
        appending = True
        results = []
        print(f"  {done_start + i:,}/{done_start + total:,} — virheita: {errors}", flush=True)
    elif i % 500 == 0:
        print(f"  {done_start + i:,}/{done_start + total:,} — virheita: {errors}", flush=True)

    time.sleep(0.05)

flush(results, OUT_CSV, appending)
print(f"\nValmis. Tallennettu: {OUT_CSV}")
print(f"Virheita: {errors} / {total}")
