"""
Pattern-louhinta proposition-aineistosta.

Lähestymistapa:
1. Jokaiselle pykälälle: onko sillä >=1 propositio luokassa X (binary per luokka)
2. Etsi n-grammeja jotka esiintyvät disproportionaalisesti pykälissä joilla on
   X-luokan propositioita
3. Lisäksi: hyödynnetään LLM:n perustelu-kenttiä, joissa usein lainataan
   suoraan kielen tunnusmerkkilauseita ('Teksti sanoo "X"')

Output: ehdokas-patternit per modaliteetti, järjestys ennustusvoiman mukaan.
"""
import re
import csv
from collections import Counter, defaultdict
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
PROPS_CSV  = ROOT / "data" / "deontic_propositions.csv"
SAMPLE_CSV = ROOT / "data" / "deontic_thk_sample.csv"

# ── Lue aineistot ─────────────────────────────────────────────────────────────

print("Luetaan...")
props = pl.read_csv(str(PROPS_CSV), infer_schema_length=0)

sample = pl.read_csv(str(SAMPLE_CSV), infer_schema_length=0)
sample = sample.filter(~pl.col("modaliteetti").is_in(["virhe","ehto","viittauslause"]))

# Indeksi: pykälä-avain → propositioiden modaliteettijoukko
pyk_mods = defaultdict(set)
for r in props.iter_rows(named=True):
    key = (r["law_id"], r["eId"], r["num"])
    pyk_mods[key].add(r["modaliteetti"])

# Yhdistä sample-tekstit ja niiden propositiojoukot
texts_by_class = defaultdict(list)
all_texts = []
for r in sample.iter_rows(named=True):
    key = (r["law_id"], r["eId"], r["num"])
    text = (r["text"] or "").lower()
    if not text:
        continue
    all_texts.append(text)
    mods = pyk_mods.get(key, set())
    if not mods:
        texts_by_class["ei_deontti_implicit"].append(text)
    else:
        for m in mods:
            texts_by_class[m].append(text)

# ── Sanaperustaiset feature-frekvenssit per luokka ────────────────────────────

print("Lasketaan n-grammit...")

def tokenize(text):
    return re.findall(r"\b[a-zåäö]+\b", text.lower())

def ngrams(tokens, n):
    return [" ".join(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]

all_grams_count = Counter()
for t in all_texts:
    toks = tokenize(t)
    for n in (1, 2, 3):
        all_grams_count.update(ngrams(toks, n))
total_tokens = sum(all_grams_count.values())

class_grams = {}
for cls, texts in texts_by_class.items():
    c = Counter()
    for t in texts:
        toks = tokenize(t)
        for n in (1, 2, 3):
            c.update(ngrams(toks, n))
    class_grams[cls] = c

# ── Pisteytys: lift-tyyppinen relevanssi luokalle ─────────────────────────────

print("\n=== TOP-PATTERNIT PER LUOKKA (>=50 osumaa, lift>=2.5) ===\n")

CLASSES_TO_SHOW = ["velvoite", "kielto", "lupa", "suositus"]

for cls in CLASSES_TO_SHOW:
    if cls not in class_grams:
        continue
    cls_size = len(texts_by_class[cls])
    if cls_size == 0:
        continue
    p_cls = sum(len(tokenize(t)) for t in texts_by_class[cls]) / total_tokens

    candidates = []
    for gram, cnt in class_grams[cls].items():
        if cnt < 50:
            continue
        bg = all_grams_count.get(gram, 0)
        if bg == 0:
            continue
        precision_est = cnt / bg
        lift = precision_est / max(p_cls, 1e-9)
        if lift < 2.5:
            continue
        candidates.append((gram, cnt, bg, precision_est, lift))

    candidates.sort(key=lambda x: x[4] * x[1], reverse=True)

    print(f"--- {cls.upper()} ({cls_size:,} pykälää joilla on {cls}-propositio) ---")
    print(f"{'pattern':<35} {'in_cls':>7} {'tot':>7} {'prec':>6} {'lift':>5}")
    for g, cnt, bg, prec, lift in candidates[:25]:
        print(f"  {g:<33} {cnt:>7,} {bg:>7,} {prec*100:>5.0f}% {lift:>4.1f}x")
    print()

# ── Bonus: LLM:n perustelu-kentistä lainattuja triggerifraaseja ──────────────
print("\n=== LLM:n perustelu-kentistä lainatut tunnusmerkkifraasit ===\n")
quote_re = re.compile(r"['\"`]([^'\"`]{3,40})['\"`]")
quotes_by_class = defaultdict(Counter)
for r in props.iter_rows(named=True):
    text = (r.get("perustelu") or "")
    for q in quote_re.findall(text.lower()):
        if 5 <= len(q) <= 40:
            quotes_by_class[r["modaliteetti"]][q] += 1

for cls in CLASSES_TO_SHOW:
    print(f"--- {cls.upper()} top-fraaseja LLM:n perusteluissa ---")
    for q, n in quotes_by_class.get(cls, Counter()).most_common(15):
        print(f"  {n:>4}x \"{q}\"")
    print()
