"""
Jakaa olemassa olevien propositioiden 'kohde'-kentän kahteen osaan:
'tehtava' (verbi) ja 'toiminnan_kohde' (objekti).

Lukee deontic_propositions.csv ja kirjoittaa
deontic_propositions_extended.csv jossa on 2 uutta saraketta. Ei muuta
muita kenttiä eikä propositioita pudoteta.

Eräajetaan 50 propositiota / LLM-kutsu, 10 rinnakkaista työntekijää.

Kaytto:
    python -u parser/split_kohde.py [--limit N]
"""
import argparse
import csv
import json
import os
import re
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

# ── Asetukset ─────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).resolve().parents[1]
IN_CSV    = ROOT / "data" / "deontic_propositions.csv"
OUT_CSV   = ROOT / "data" / "deontic_propositions_extended.csv"
DONE_LOG  = ROOT / "data" / "split_kohde_done.txt"
MODEL     = "claude-haiku-4-5-20251001"
BATCH     = 50           # propositiota per LLM-kutsu
WORKERS   = 10           # rinnakkaisia kutsuja

# ── Argumentit ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=None,
                    help="Käsittele vain ensimmaiset N propositiota (pilotti)")
args = parser.parse_args()

# ── Lue lähde ─────────────────────────────────────────────────────────────────

print(f"Luetaan {IN_CSV.name}...")
rows: list[dict] = []
with open(IN_CSV, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))
print(f"  Propositioita: {len(rows):,}")

# Anna jokaiselle riville yksilöivä id (rivi-indeksi)
for i, r in enumerate(rows):
    r["_idx"] = i

# Resume-tila
done_idx: set[int] = set()
if DONE_LOG.exists():
    done_idx = {int(l.strip()) for l in DONE_LOG.read_text(encoding="utf-8").splitlines() if l.strip()}
    print(f"  Resume: {len(done_idx):,} propositiota jo käsitelty")

todo = [r for r in rows if r["_idx"] not in done_idx and (r.get("kohde") or "").strip()]
if args.limit:
    todo = todo[:args.limit]
    print(f"  PILOTTI: rajattu {args.limit} propositioon")

print(f"  Käsitellään: {len(todo):,} propositiota")
if not todo:
    print("Ei mitään tehtävää.")
    sys.exit(0)

# Valmistele eräät
batches: list[list[dict]] = []
for i in range(0, len(todo), BATCH):
    batches.append(todo[i:i+BATCH])
print(f"  {len(batches):,} eräää (BATCH={BATCH})")

# ── Output-CSV ────────────────────────────────────────────────────────────────
# Sarakkeet: kaikki vanhat + tehtava + toiminnan_kohde

OUT_COLS = list(rows[0].keys())
if "_idx" in OUT_COLS:
    OUT_COLS.remove("_idx")
OUT_COLS = OUT_COLS + ["tehtava", "toiminnan_kohde"]

# Jos OUT_CSV on jo olemassa (resume), avataan append-tilassa, muutoin uusi
new_file = not OUT_CSV.exists()
out_f = open(OUT_CSV, "a", encoding="utf-8", newline="")
writer = csv.DictWriter(out_f, fieldnames=OUT_COLS, extrasaction="ignore")
if new_file:
    writer.writeheader()
    out_f.flush()

done_f = open(DONE_LOG, "a", encoding="utf-8")

# ── LLM-kutsu ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Olet suomenkielisen lakitekstin analysoija. Sinulle annetaan
lista deonttisia propositioita, joista jokaisesta tunnetaan jo toimija ja
modaliteetti. Tehtävänäsi on jakaa kunkin proposition `kohde`-merkkijono
kahteen osaan:

- `tehtava`: verbi/predikaatti — mitä toimija tekee tai mitä toimitetaan.
  Yleensä infinitiivi (saada, antaa, ilmoittaa, järjestää, tehdä, luovuttaa).
- `toiminnan_kohde`: substantiivilauseke — mitä asiaa, esinettä tai tilannetta
  toiminta kohtaa.

Esimerkkejä:
  "saada korvaus jälleenmyynnistä"
    -> tehtava="saada", toiminnan_kohde="korvaus jälleenmyynnistä"
  "luovuttaa oikeutta kolmannelle"
    -> tehtava="luovuttaa", toiminnan_kohde="oikeus kolmannelle"
  "järjestää sosiaalipalvelut asukkailleen"
    -> tehtava="järjestää", toiminnan_kohde="sosiaalipalvelut asukkailleen"
  "ilmoittaa muutoksesta määräajassa"
    -> tehtava="ilmoittaa", toiminnan_kohde="muutos määräajassa"

Jos kohde on hyvin lyhyt eikä siinä ole erotettavissa kohdetta (esim. pelkkä
verbi), aseta `toiminnan_kohde` tyhjäksi merkkijonoksi.

Palauta JSON-lista jossa jokaiselle annetulle proposition id:lle on yksi
merkintä:

[
  {"id": 1, "tehtava": "...", "toiminnan_kohde": "..."},
  {"id": 2, "tehtava": "...", "toiminnan_kohde": "..."},
  ...
]

Älä lisää mitään muuta tekstiä, vain JSON-lista."""


def build_user_prompt(batch: list[dict]) -> str:
    lines = []
    for i, r in enumerate(batch, 1):
        toimija = (r.get("toimija") or "").strip()
        mod     = (r.get("modaliteetti") or "").strip()
        kohde   = (r.get("kohde") or "").strip()
        ctx     = f"({mod}, toimija={toimija!r})" if (mod or toimija) else ""
        lines.append(f"{i}. {ctx} kohde={kohde!r}")
    return "Jaa seuraavien propositioiden kohde-kentät:\n\n" + "\n".join(lines) + "\n\nPalauta JSON-lista."


def parse_response(raw: str, batch_size: int) -> list[dict | None]:
    """Palauttaa lista [batch_size] jossa kullekin id:lle joko {tehtava, toiminnan_kohde} tai None."""
    result: list[dict | None] = [None] * batch_size
    # Etsi JSON-lohko
    m = re.search(r"\[[\s\S]+\]", raw)
    if not m:
        return result
    try:
        items = json.loads(m.group())
    except json.JSONDecodeError:
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id", 0)) - 1
        except (ValueError, TypeError):
            continue
        if 0 <= idx < batch_size:
            result[idx] = {
                "tehtava":         str(item.get("tehtava", "")).strip()[:60],
                "toiminnan_kohde": str(item.get("toiminnan_kohde", "")).strip()[:200],
            }
    return result


client = anthropic.Anthropic(max_retries=2)


def process_batch(batch: list[dict]) -> tuple[list[dict], list[dict | None], str | None]:
    user_prompt = build_user_prompt(batch)
    try:
        response = client.messages.create(
            model      = MODEL,
            max_tokens = 4096,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_prompt}],
            timeout    = 60.0,
        )
        raw = response.content[0].text
        results = parse_response(raw, len(batch))
        return (batch, results, None)
    except Exception as e:
        return (batch, [None] * len(batch), str(e)[:120])


# ── Aja rinnakkain ────────────────────────────────────────────────────────────

t0 = time.time()
n_processed = 0
n_split_ok = 0
n_errors = 0
n_failed_items = 0

print()
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = {pool.submit(process_batch, b): b for b in batches}
    pending_writes: list[dict] = []
    pending_done:   list[int]  = []

    for fut in as_completed(futures):
        batch, results, err = fut.result()
        if err:
            n_errors += 1

        for r, split_result in zip(batch, results):
            row_out = {k: r.get(k, "") for k in OUT_COLS if k != "tehtava" and k != "toiminnan_kohde"}
            if split_result is None:
                # Jako epäonnistui — kirjataan rivi tyhjillä uusilla kentillä
                row_out["tehtava"] = ""
                row_out["toiminnan_kohde"] = ""
                n_failed_items += 1
            else:
                row_out["tehtava"] = split_result["tehtava"]
                row_out["toiminnan_kohde"] = split_result["toiminnan_kohde"]
                n_split_ok += 1
            pending_writes.append(row_out)
            pending_done.append(r["_idx"])

        n_processed += len(batch)

        # Flush periodisesti (joka 5. erä)
        if len(pending_writes) >= BATCH * 5:
            writer.writerows(pending_writes)
            out_f.flush()
            done_f.write("\n".join(str(i) for i in pending_done) + "\n")
            done_f.flush()
            pending_writes.clear()
            pending_done.clear()

            elapsed = time.time() - t0
            rate = n_processed / elapsed if elapsed else 0
            eta = (len(todo) - n_processed) / rate if rate else 0
            print(f"  {n_processed:,}/{len(todo):,}  jaettuja: {n_split_ok:,}  "
                  f"epäonnistuneet rivit: {n_failed_items}  "
                  f"vauhti: {rate:.0f}/s  ETA: {eta/60:.0f} min", flush=True)

    if pending_writes:
        writer.writerows(pending_writes)
    if pending_done:
        done_f.write("\n".join(str(i) for i in pending_done) + "\n")

out_f.close()
done_f.close()

elapsed = time.time() - t0
print(f"\nValmis: {n_processed:,} propositiota, {n_split_ok:,} jaettu onnistuneesti")
print(f"Erä-virheet: {n_errors}, rivivirheet: {n_failed_items}")
print(f"Aika: {elapsed/60:.1f} min")
print(f"Output: {OUT_CSV}")
