"""
Propositio-tason annotointi: kayttaa olemassa olevaa LLM-otosta
(deontic_thk_sample.csv) ja annotoi kunkin pykälän propositio-tasolla
useaksi (toimija, modaliteetti, kohde) -kolmikoksi.

Output: data/deontic_propositions.csv
  - Yksi rivi per propositio (ei per pykälä)
  - Sarakkeet: law_id, eId, num, law_title, org_tyyppi, prop_id,
               modaliteetti, toimija, kohde, type, perustelu, text

Resume-logiikka: lukee jo käsitellyt pykälät tunnisteparilla
(law_id, eId, num) ja ohittaa ne. Jos pykälällä on jo edes yksi
propositio outputissa, sitä ei ajeta uudelleen — myöskään tyhjiä
pykäliä ei ajeta uudelleen, koska kirjaamme niillekin yhden
"_status"-rivin.

Rinnakkaisuus: 10 työntekijää ThreadPoolExecutorilla. Resume-merkinnät
kirjoitetaan kun batch on valmis (joka 100 pykälää).

Kaytto:
    python -u parser/annotate_propositions.py [--limit N]

    --limit N  → annotoi vain ensimmäiset N pykälää (pilottiajoa varten)
"""
import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from proposition_prompt import SYSTEM_PROMPT, build_user_prompt, parse_response

# ── Asetukset ─────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).resolve().parents[1]
IN_CSV    = ROOT / "data" / "deontic_thk_sample.csv"
OUT_CSV   = ROOT / "data" / "deontic_propositions.csv"
DONE_LOG  = ROOT / "data" / "deontic_propositions_done.txt"  # status per pykälä
MODEL     = "claude-haiku-4-5-20251001"
WORKERS   = 10
SAVE_EVERY = 100   # tallennetaan välitulokset joka 100 pykälän valmistuttua

# ── Argumentit ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=None,
                    help="Annotoi vain ensimmaiset N pykälää (pilotti)")
args = parser.parse_args()

# ── Lue lähde ─────────────────────────────────────────────────────────────────

print(f"Luetaan {IN_CSV.name}...")
rows = []
with open(IN_CSV, encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        if r.get("modaliteetti", "").strip().lower() in {"virhe", "ehto", "viittauslause"}:
            continue
        text = (r.get("text") or "").strip()
        if not text or len(text) < 20:
            continue
        rows.append(r)
print(f"  Pykälää: {len(rows):,}")

# ── Resume: lue jo tehdyt pykälät done-logista ───────────────────────────────

def row_key(r) -> str:
    return f"{r.get('law_id','')}|{r.get('eId','')}|{r.get('num','')}"

done: set[str] = set()
if DONE_LOG.exists():
    done = set(DONE_LOG.read_text(encoding="utf-8").splitlines())
    done.discard("")
    print(f"  Resume: {len(done):,} pykälää jo tehty")

todo = [r for r in rows if row_key(r) not in done]
if args.limit:
    todo = todo[:args.limit]
    print(f"  PILOTTI: rajattu {args.limit} pykälään")

print(f"  Annotoidaan: {len(todo):,} pykälää  ({MODEL}, {WORKERS} rinnakkaista)")
if not todo:
    print("Ei mitään tehtävää.")
    sys.exit(0)

# ── Output-CSV: avaa append-moodissa, kirjoita header jos uusi ────────────────

COLS = ["law_id", "eId", "num", "law_title", "org_tyyppi",
        "prop_id", "modaliteetti", "toimija", "kohde", "type",
        "perustelu", "text"]

new_file = not OUT_CSV.exists()
out_f = open(OUT_CSV, "a", encoding="utf-8", newline="")
writer = csv.DictWriter(out_f, fieldnames=COLS, extrasaction="ignore")
if new_file:
    writer.writeheader()
    out_f.flush()

done_f = open(DONE_LOG, "a", encoding="utf-8")

# ── LLM-asiakas ───────────────────────────────────────────────────────────────

client = anthropic.Anthropic(max_retries=2)

def annotate(row: dict) -> tuple[dict, list[dict], str | None]:
    """Annotoi yksi pykälä. Palauttaa (row, propositiot, virhe)."""
    text = (row.get("text") or "").strip()
    user_prompt = build_user_prompt(
        text      = text[:3500],
        law_title = row.get("law_title", ""),
        section   = row.get("num") or row.get("eId") or "",
    )
    try:
        response = client.messages.create(
            model      = MODEL,
            max_tokens = 1024,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_prompt}],
            timeout    = 60.0,
        )
        raw   = response.content[0].text
        props = parse_response(raw)
        return (row, props, None)
    except Exception as e:
        return (row, [], str(e)[:120])

# ── Aja rinnakkain ────────────────────────────────────────────────────────────

t0 = time.time()
n_processed = 0
n_props = 0
n_errors = 0

print()
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = {pool.submit(annotate, r): r for r in todo}
    pending_writes: list[dict] = []
    pending_done:   list[str]  = []

    for fut in as_completed(futures):
        row, props, err = fut.result()
        n_processed += 1

        if err:
            n_errors += 1
            # Virheellisiä EI merkitä tehdyiksi -> resume yrittää niitä uudelleen
            continue

        # Kirjaa propositiot (myös 0 kpl tapaus → ei riveja, mutta status kirjataan)
        for i, p in enumerate(props, 1):
            pending_writes.append({
                "law_id":       row.get("law_id", ""),
                "eId":          row.get("eId", ""),
                "num":          row.get("num", ""),
                "law_title":    (row.get("law_title") or "")[:120],
                "org_tyyppi":   row.get("org_tyyppi", ""),
                "prop_id":      i,
                "modaliteetti": p["modaliteetti"],
                "toimija":      p["toimija"][:80],
                "kohde":        p["kohde"][:200],
                "type":         p["type"],
                "perustelu":    p["perustelu"],
                "text":         (row.get("text") or "")[:300],
            })
            n_props += 1

        # Onnistunut (joko proppoja tai laillisesti tyhjä) -> merkitään tehdyksi
        pending_done.append(row_key(row))

        # Flush periodisesti
        if len(pending_done) >= SAVE_EVERY:
            writer.writerows(pending_writes)
            out_f.flush()
            done_f.write("\n".join(pending_done) + "\n")
            done_f.flush()
            pending_writes.clear()
            pending_done.clear()

            elapsed = time.time() - t0
            rate = n_processed / elapsed if elapsed else 0
            eta = (len(todo) - n_processed) / rate if rate else 0
            print(f"  {n_processed:,}/{len(todo):,}  propositioita: {n_props:,}  "
                  f"virheita: {n_errors}  vauhti: {rate:.1f}/s  ETA: {eta/60:.0f} min",
                  flush=True)

    # Flush loput
    if pending_writes:
        writer.writerows(pending_writes)
    if pending_done:
        done_f.write("\n".join(pending_done) + "\n")

out_f.close()
done_f.close()

elapsed = time.time() - t0
print(f"\nValmis: {n_processed:,} pykälää, {n_props:,} propositiota "
      f"({n_props / n_processed if n_processed else 0:.2f} per pykälä)")
print(f"Virheita: {n_errors}")
print(f"Aika: {elapsed/60:.1f} min")
print(f"Output: {OUT_CSV}")
