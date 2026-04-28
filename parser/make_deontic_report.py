"""
Rakentaa interaktiivisen HTML-raportin deonttisesta annotaatiosta.

Näyttää kaikki 6163 riviä suodatettavassa taulukossa:
  - Yhteenvetotilastot per org-tyyppi ja luokka
  - Suodattimet: org / LLM-luokka / regex-luokka / vain virheet
  - Hakukenttä tekstiin
  - Confusion matrix

Käyttö:
    python parser/make_deontic_report.py

Output: data/deontic_report.html
"""
import csv
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deontic_classifier import classify

ROOT     = Path(__file__).resolve().parents[1]
IN_CSV   = ROOT / "data" / "deontic_thk_sample.csv"
OUT_HTML = ROOT / "data" / "deontic_report.html"

CATS = ["velvoite", "lupa", "kielto", "suositus", "ei_deontti"]
ORGS = ["HYVINVOINTIALUE", "KUNTA", "VALTIO",
        "RIKOS", "VERO", "YKSITYIS", "YRITYS", "TYO", "HALLINTO", "ERIKOIS"]

CAT_COLOR = {
    "velvoite":   "#2471a3",
    "lupa":       "#1e8449",
    "kielto":     "#c0392b",
    "suositus":   "#d68910",
    "ei_deontti": "#7f8c8d",
}

# ── Lue data ──────────────────────────────────────────────────────────────────

print("Luetaan ja luokitellaan...")
rows = []
law_ids = set()
with open(IN_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        llm   = row.get("modaliteetti", "").strip().lower()
        text  = row.get("text", "").strip()
        org   = row.get("org_tyyppi", "").strip()
        if not llm or not text:
            continue
        # Suodata pois LLM:n virheelliset / epäselvät rivit
        if llm in {"virhe", "ehto", "viittauslause"}:
            continue
        regex = classify(text)
        law_ids.add(row.get("law_id", ""))
        rows.append({
            "i":     len(rows),
            "llm":   llm,
            "regex": regex,
            "org":   org,
            "law":   row.get("law_title", "")[:60],
            "num":   row.get("num", "") or row.get("eId", ""),
            "text":  text[:400],
            "ok":    llm == regex,
            "subj":  (row.get("oikeussubjekti") or "").strip(),
            "perust": (row.get("perustelu") or "").strip()[:300],
        })

n_laws = len(law_ids - {""})
print(f"  Rivejä: {len(rows):,}")
print(f"  Lakeja: {n_laws}")

# ── Toimija-aggregaatti ──────────────────────────────────────────────────────
# LLM kirjasi kullekin pykälälle oikeussubjektin (toimijan). Aggregoidaan se
# normalisoidulla nimellä: ryhmitellään pieni-/isokirjainerot ja triviaalit
# kirjoitusasut. Säilytetään yleisin kirjoitusasu näytettäväksi.
from collections import Counter

def norm_subj(s):
    return s.strip().lower() if s else ""

subj_groups: dict[str, list[int]] = defaultdict(list)
subj_display: dict[str, Counter] = defaultdict(Counter)
for r in rows:
    if not r["subj"]:
        continue
    key = norm_subj(r["subj"])
    subj_groups[key].append(r["i"])
    subj_display[key][r["subj"]] += 1

toimijat = []
for key, idxs in subj_groups.items():
    counts = Counter(rows[i]["llm"] for i in idxs)
    display = subj_display[key].most_common(1)[0][0]
    toimijat.append({
        "name":    display,
        "key":     key,
        "total":   len(idxs),
        "velvoite":  counts.get("velvoite", 0),
        "lupa":      counts.get("lupa", 0),
        "kielto":    counts.get("kielto", 0),
        "suositus":  counts.get("suositus", 0),
        "ei_deontti": counts.get("ei_deontti", 0),
        "rows":    idxs,
    })
toimijat.sort(key=lambda t: t["total"], reverse=True)
n_with_subj = sum(1 for r in rows if r["subj"])
print(f"  Toimijoita: {len(toimijat):,} (oikeussubjekti tunnistettu {n_with_subj:,}/{len(rows):,} rivillä)")

# Tallennetaan myos CSV asiakkaan kayttoon
TOIMIJA_CSV = ROOT / "data" / "toimija_velvoitteet.csv"
with open(TOIMIJA_CSV, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["organisaatio", "modaliteetti", "org_tyyppi", "law_title",
                "pykala", "perustelu", "teksti"])
    for r in rows:
        if not r["subj"]:
            continue
        w.writerow([r["subj"], r["llm"], r["org"], r["law"],
                    r["num"], r["perust"], r["text"]])
print(f"  CSV: {TOIMIJA_CSV.name}")

# ── Tilastot ──────────────────────────────────────────────────────────────────

def stats(data):
    total   = len(data)
    correct = sum(1 for r in data if r["ok"])
    by_class = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for r in data:
        if r["ok"]:
            by_class[r["llm"]]["tp"] += 1
        else:
            by_class[r["llm"]]["fn"] += 1
            by_class[r["regex"]]["fp"] += 1
    result = {"total": total, "correct": correct, "acc": correct / total if total else 0, "cats": {}}
    for cat in CATS:
        d = by_class[cat]
        tp, fp, fn = d["tp"], d["fp"], d["fn"]
        prec = tp / (tp + fp) if tp + fp else 0
        rec  = tp / (tp + fn) if tp + fn else 0
        f1   = 2 * prec * rec / (prec + rec) if prec + rec else 0
        result["cats"][cat] = {"tp": tp, "fp": fp, "fn": fn,
                                "prec": prec, "rec": rec, "f1": f1}
    return result

all_stats  = stats(rows)
org_stats  = {org: stats([r for r in rows if r["org"] == org]) for org in ORGS}

# Confusion matrix: llm → regex → count
conf = defaultdict(lambda: defaultdict(int))
for r in rows:
    conf[r["llm"]][r["regex"]] += 1

# ── JSON data ─────────────────────────────────────────────────────────────────

data_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
stats_json = json.dumps({
    "all": all_stats, "orgs": org_stats,
    "conf": {k: dict(v) for k, v in conf.items()},
    "colors": CAT_COLOR,
}, ensure_ascii=False, separators=(",", ":"))
toimijat_json = json.dumps(toimijat, ensure_ascii=False, separators=(",", ":"))

print(f"  JSON: {len(data_json)//1024:,} KB")

# ── HTML ──────────────────────────────────────────────────────────────────────

def pct(v):
    return f"{v*100:.1f}%"

def stats_table_html(s, label):
    acc_color = "#1e8449" if s["acc"] >= 0.75 else "#d68910" if s["acc"] >= 0.60 else "#c0392b"
    rows_html = ""
    for cat in CATS:
        c = s["cats"][cat]
        f1c = "#1e8449" if c["f1"] >= 0.70 else "#d68910" if c["f1"] >= 0.50 else "#c0392b"
        rows_html += f"""<tr>
          <td><span class="badge" style="background:{CAT_COLOR[cat]}">{cat}</span></td>
          <td class="num">{pct(c['prec'])}</td>
          <td class="num">{pct(c['rec'])}</td>
          <td class="num" style="color:{f1c};font-weight:600">{pct(c['f1'])}</td>
          <td class="num light">{c['tp']}/{c['fn']}/{c['fp']}</td>
        </tr>"""
    return f"""
    <div class="stat-card">
      <div class="stat-header">
        {label}
        <span class="acc-badge" style="background:{acc_color}">{pct(s['acc'])}</span>
        <span class="light">n={s['total']:,}</span>
      </div>
      <table class="stat-tbl">
        <tr><th>Luokka</th><th>Prec</th><th>Rec</th><th>F1</th><th>TP/FN/FP</th></tr>
        {rows_html}
      </table>
    </div>"""

stats_html = stats_table_html(all_stats, "Kaikki")
for org in ORGS:
    stats_html += stats_table_html(org_stats[org], org.capitalize())

html = f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<title>Deonttinen analyysi — raportti</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: system-ui, sans-serif; background: #f0f2f5; color: #2d3436; font-size: 13px; }}

/* ── Layout ── */
#app {{ display: flex; flex-direction: column; height: 100vh; }}
#top {{ background: #2d3436; color: #fff; padding: 10px 16px; flex-shrink: 0; }}
#top h1 {{ font-size: 15px; color: #b2bec3; display: inline; margin-right: 16px; }}
#stats-bar {{
  display: flex; gap: 12px; overflow-x: auto; padding: 12px 16px;
  background: #fff; border-bottom: 1px solid #dfe6e9; flex-shrink: 0;
}}
#filters {{
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  padding: 8px 16px; background: #fff; border-bottom: 1px solid #dfe6e9;
  flex-shrink: 0;
}}
#table-wrap {{ flex: 1; overflow-y: auto; padding: 0; }}

/* ── Stat cards ── */
.stat-card {{ min-width: 220px; flex-shrink: 0; }}
.stat-header {{
  font-weight: 600; font-size: 12px; padding: 4px 0 6px 0;
  display: flex; align-items: center; gap: 8px; text-transform: uppercase;
  letter-spacing: 0.04em; color: #636e72;
}}
.acc-badge {{
  display: inline-block; padding: 1px 7px; border-radius: 10px;
  font-size: 12px; color: #fff; font-weight: 700;
}}
.stat-tbl {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
.stat-tbl th {{ color: #b2bec3; font-weight: 500; padding: 2px 6px 2px 0; text-align: left; }}
.stat-tbl td {{ padding: 2px 6px 2px 0; }}

/* ── Confusion matrix ── */
#conf-wrap {{ min-width: 260px; flex-shrink: 0; }}
.conf-tbl {{ border-collapse: collapse; font-size: 11px; }}
.conf-tbl th {{ padding: 3px 6px; color: #636e72; font-weight: 500; }}
.conf-tbl td {{
  padding: 3px 6px; text-align: center; min-width: 34px;
  border: 1px solid #f0f0f0;
}}
.conf-diag {{ font-weight: 700; }}

/* ── Filters ── */
select, input[type=text] {{
  padding: 5px 9px; border: 1px solid #dfe6e9; border-radius: 4px;
  background: #fff; font-size: 13px; color: #2d3436;
}}
label {{ font-size: 12px; color: #636e72; }}
#count {{ font-size: 12px; color: #636e72; margin-left: auto; }}
.filter-check {{ display: flex; align-items: center; gap: 5px; cursor: pointer; }}
.filter-check input {{ width: 14px; height: 14px; cursor: pointer; }}

/* ── Taulukko ── */
table {{ width: 100%; border-collapse: collapse; }}
thead th {{
  position: sticky; top: 0; background: #f8f9fa;
  padding: 8px 12px; text-align: left; font-size: 12px;
  color: #636e72; text-transform: uppercase; letter-spacing: 0.04em;
  border-bottom: 2px solid #dfe6e9; z-index: 1;
}}
tbody tr {{ border-bottom: 1px solid #f0f0f0; }}
tbody tr:hover {{ background: #f8f9fa; }}
tbody tr.mismatch {{ background: #fff8f8; }}
tbody tr.mismatch:hover {{ background: #ffeaea; }}
td {{ padding: 7px 12px; vertical-align: top; }}
.badge {{
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600; color: #fff; white-space: nowrap;
}}
.org-badge {{ font-size: 11px; color: #636e72; font-weight: 500; }}
.law-name {{ color: #636e72; font-size: 11px; }}
.text-cell {{ max-width: 500px; line-height: 1.5; color: #2d3436; }}
.text-cell.truncated {{ display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
.num {{ text-align: right; }}
.light {{ color: #b2bec3; }}
.match-icon {{ font-size: 14px; }}

/* ── Välilehdet ── */
#tabs {{ display: flex; gap: 0; background: #2d3436; padding: 0 16px; flex-shrink: 0; }}
.tab {{
  padding: 10px 18px; cursor: pointer; font-size: 13px; color: #b2bec3;
  border-bottom: 3px solid transparent; margin-bottom: -1px;
  user-select: none;
}}
.tab:hover {{ color: #fff; }}
.tab.active {{ color: #fff; border-bottom-color: #74b9ff; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: flex; flex-direction: column; flex: 1; overflow: hidden; }}

/* ── Info-välilehti ── */
#info-content {{
  flex: 1; overflow-y: auto; padding: 32px 40px; max-width: 860px;
}}
#info-content h2 {{
  font-size: 20px; font-weight: 700; margin-bottom: 8px; color: #2d3436;
}}
#info-content h3 {{
  font-size: 14px; font-weight: 700; margin: 24px 0 8px 0;
  color: #2d3436; text-transform: uppercase; letter-spacing: 0.05em;
}}
#info-content p {{ line-height: 1.7; color: #4a4a4a; margin-bottom: 10px; }}
#info-content ul {{ padding-left: 20px; line-height: 1.8; color: #4a4a4a; margin-bottom: 10px; }}
#info-content .step {{
  display: flex; gap: 16px; margin-bottom: 20px; align-items: flex-start;
}}
#info-content .step-num {{
  flex-shrink: 0; width: 28px; height: 28px; border-radius: 50%;
  background: #2d3436; color: #fff; display: flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: 13px; margin-top: 2px;
}}
#info-content .step-body h4 {{ font-size: 14px; font-weight: 600; margin-bottom: 4px; }}
#info-content .step-body p {{ margin: 0; }}
#info-content .pill {{
  display: inline-block; padding: 2px 10px; border-radius: 12px;
  font-size: 12px; font-weight: 600; color: #fff; margin: 2px 3px 2px 0;
  vertical-align: middle;
}}
.divider {{ border: none; border-top: 1px solid #dfe6e9; margin: 24px 0; }}

/* ── Toimija-välilehti ── */
#toimija-layout {{ display: flex; flex: 1; overflow: hidden; }}
#toimija-list {{
  width: 480px; flex-shrink: 0; border-right: 1px solid #dfe6e9;
  background: #fff; overflow-y: auto;
}}
#toimija-list table {{ width: 100%; border-collapse: collapse; }}
#toimija-list th {{
  position: sticky; top: 0; background: #f8f9fa; padding: 8px 10px;
  text-align: left; font-size: 11px; color: #636e72;
  text-transform: uppercase; letter-spacing: 0.04em;
  border-bottom: 2px solid #dfe6e9; z-index: 1;
}}
#toimija-list td {{ padding: 6px 10px; border-bottom: 1px solid #f0f0f0; }}
#toimija-list tr {{ cursor: pointer; }}
#toimija-list tr:hover {{ background: #f8f9fa; }}
#toimija-list tr.selected {{ background: #e3f2fd; }}
#toimija-list tr.selected td {{ font-weight: 600; }}
.t-count {{
  display: inline-block; min-width: 28px; padding: 1px 6px;
  border-radius: 10px; font-size: 11px; color: #fff;
  text-align: center; font-weight: 600;
}}
#toimija-detail {{
  flex: 1; overflow-y: auto; padding: 16px 24px; background: #f0f2f5;
}}
#toimija-detail h3 {{
  font-size: 16px; font-weight: 700; margin-bottom: 6px; color: #2d3436;
}}
#toimija-detail .summary {{
  display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap;
}}
#toimija-detail .pykala {{
  background: #fff; border: 1px solid #dfe6e9; border-radius: 4px;
  padding: 12px 14px; margin-bottom: 8px;
}}
#toimija-detail .pykala-header {{
  display: flex; gap: 8px; align-items: center; margin-bottom: 6px;
  font-size: 12px;
}}
#toimija-detail .pykala-text {{
  color: #2d3436; line-height: 1.5; font-size: 13px;
}}
#toimija-detail .perustelu {{
  color: #636e72; font-size: 12px; font-style: italic;
  margin-top: 6px; padding-top: 6px; border-top: 1px dashed #dfe6e9;
}}
#toimija-search {{ padding: 8px 10px; border-bottom: 1px solid #dfe6e9; background: #fff; }}
#toimija-search input {{ width: 100%; padding: 6px 10px; }}
#toimija-filters {{
  padding: 8px 16px; background: #fff; border-bottom: 1px solid #dfe6e9;
  display: flex; gap: 12px; align-items: center; flex-shrink: 0;
}}
</style>
</head>
<body>
<div id="app">

<div id="top" style="display:flex;align-items:center;gap:16px">
  <h1>&#9654; Deonttinen analyysi</h1>
  <span style="color:#b2bec3;font-size:12px">LLM vs. regex-klassifikaattori · {len(rows):,} pykälää</span>
</div>

<div id="tabs">
  <div class="tab active" onclick="switchTab('report')">Tulokset</div>
  <div class="tab" onclick="switchTab('toimijat')">Toimijat &amp; tehtävät</div>
  <div class="tab" onclick="switchTab('info')">&#9432; Tietoa analyysista</div>
</div>

<!-- ── TULOKSET-VÄLILEHTI ── -->
<div id="tab-report" class="tab-content active">

<div id="stats-bar">
  {stats_html}
  <div id="conf-wrap">
    <div class="stat-header">Confusion matrix <span class="light">(LLM rivi → regex sarake)</span></div>
    <div id="conf-table"></div>
  </div>
</div>

<div id="filters">
  <label>Org:</label>
  <select id="f-org" onchange="applyFilters()">
    <option value="">Kaikki</option>
    <option>HYVINVOINTIALUE</option>
    <option>KUNTA</option>
    <option>VALTIO</option>
  </select>
  <label>LLM:</label>
  <select id="f-llm" onchange="applyFilters()">
    <option value="">Kaikki</option>
    <option>velvoite</option><option>lupa</option><option>kielto</option>
    <option>suositus</option><option>ei_deontti</option>
  </select>
  <label>Regex:</label>
  <select id="f-regex" onchange="applyFilters()">
    <option value="">Kaikki</option>
    <option>velvoite</option><option>lupa</option><option>kielto</option>
    <option>suositus</option><option>ei_deontti</option>
  </select>
  <label class="filter-check">
    <input type="checkbox" id="f-errors" onchange="applyFilters()"> Vain virheet
  </label>
  <input type="text" id="f-text" placeholder="Hae tekstistä tai lain nimestä..." oninput="applyFilters()" style="min-width:240px">
  <span id="count"></span>
</div>

<div id="table-wrap">
  <table>
    <thead>
      <tr>
        <th style="width:32px"></th>
        <th style="width:90px">LLM</th>
        <th style="width:90px">Regex</th>
        <th style="width:100px">Org</th>
        <th>Laki / Pykälä</th>
        <th>Teksti</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

</div><!-- /tab-report -->

<!-- ── TOIMIJAT-VÄLILEHTI ── -->
<div id="tab-toimijat" class="tab-content">

<div id="toimija-filters">
  <strong style="font-size:13px">Toimijat ja niihin kohdistuvat säännökset</strong>
  <span class="light" id="toimija-summary"></span>
  <label style="margin-left:auto">Org-tyyppi:</label>
  <select id="t-org" onchange="renderToimijat()">
    <option value="">Kaikki</option>
    <option>HYVINVOINTIALUE</option>
    <option>KUNTA</option>
    <option>VALTIO</option>
  </select>
  <label>Modaliteetti:</label>
  <select id="t-mod" onchange="renderToimijat()">
    <option value="">Kaikki</option>
    <option value="velvoite">velvoite</option>
    <option value="lupa">lupa</option>
    <option value="kielto">kielto</option>
    <option value="suositus">suositus</option>
    <option value="ei_deontti">ei_deontti</option>
  </select>
</div>

<div id="toimija-layout">
  <div id="toimija-list">
    <div id="toimija-search">
      <input type="text" id="t-search" placeholder="Hae toimijaa..." oninput="renderToimijat()">
    </div>
    <table>
      <thead>
        <tr>
          <th>Toimija</th>
          <th class="num" style="text-align:right">Yht.</th>
          <th class="num" style="text-align:right">velv</th>
          <th class="num" style="text-align:right">lupa</th>
          <th class="num" style="text-align:right">kiel</th>
          <th class="num" style="text-align:right">suos</th>
        </tr>
      </thead>
      <tbody id="t-tbody"></tbody>
    </table>
  </div>
  <div id="toimija-detail">
    <p class="light" style="margin-top:40px;text-align:center">
      Valitse toimija vasemmalta nähdäksesi kaikki sitä koskevat pykälät.
    </p>
  </div>
</div>

</div><!-- /tab-toimijat -->

<!-- ── INFO-VÄLILEHTI ── -->
<div id="tab-info" class="tab-content">
<div id="info-content">

  <h2>Deonttinen analyysi — menetelmä ja aineisto</h2>
  <p>Tämä työkalu luokittelee Suomen voimassa olevien lakien pykälät
  deonttisen modaliteetin mukaan: <em>velvoite, lupa, kielto, suositus</em> tai
  <em>ei-deontti</em>. Raportti vertaa kahta luokittelumenetelmää:
  kielimalliperustaista (LLM) annotaatiota ja sääntöperustaista
  (regex) luokitinta.</p>

  <p style="background:#fff8e1;padding:12px 16px;border-left:3px solid #d68910;border-radius:4px">
    <strong>Käyttöohje:</strong> Tulokset-välilehdellä voit selata kaikkia
    luokiteltuja pykäliä. Suodata näkymää organisaatiotyypin, luokan tai
    tekstihaun perusteella. <em>Vain virheet</em> -valinta näyttää tapaukset,
    joissa LLM ja regex eroavat toisistaan.
  </p>

  <hr class="divider">
  <h3>1. Aineiston hankinta ja käsittely</h3>

  <div class="step">
    <div class="step-num">1</div>
    <div class="step-body">
      <h4>Lainsäädännön lataus ja parsinta</h4>
      <p>Lähdeaineistona on Finlexin avoin data: kaikki voimassa olevat
      suomenkieliset säädökset koneluettavassa AKN-muotoisessa XML-rakenteessa.
      Jokaisesta laista poimittiin rakenteiset elementit: luvut, pykälät,
      momentit ja alakohdat. Aineiston laajuus on noin 4 GB pakattuna ja
      <strong>124 414 pykälää</strong>.</p>
    </div>
  </div>

  <div class="step">
    <div class="step-num">2</div>
    <div class="step-body">
      <h4>Pykälätasoinen aggregointi</h4>
      <p>Yksittäiset XML-elementit koottiin pykälätason tietueiksi: jokaisen
      pykälän kaikki lapsielementit (momentit, alakohdat) yhdistettiin yhdeksi
      tekstikentäksi. Näin sekä kielimalli että regex-luokitin saavat koko
      asiayhteyden eikä vain yksittäistä virkettä — tämä on välttämätöntä,
      sillä deonttinen modaliteetti määräytyy usein ympäröivän kontekstin
      perusteella.</p>
    </div>
  </div>

  <div class="step">
    <div class="step-num">3</div>
    <div class="step-body">
      <h4>Otoksen ensimmäinen vaihe — tiedonhallintakartta</h4>
      <p>Otokseen valittiin ensimmäisessä vaiheessa lait, jotka esiintyvät
      julkishallinnon tiedonhallintakartassa. Lait ryhmiteltiin
      organisaatiotyypeittäin:</p>
      <ul>
        <li><span class="pill" style="background:#c0392b">Hyvinvointialue</span>
            <strong>{org_stats['HYVINVOINTIALUE']['total']:,} pykälää</strong> (kaikki mukaan)</li>
        <li><span class="pill" style="background:#2471a3">Kunta</span>
            <strong>{org_stats['KUNTA']['total']:,} pykälää</strong> (satunnaisotos)</li>
        <li><span class="pill" style="background:#1e8449">Valtio</span>
            <strong>{org_stats['VALTIO']['total']:,} pykälää</strong> (satunnaisotos)</li>
      </ul>
    </div>
  </div>

  <div class="step">
    <div class="step-num">4</div>
    <div class="step-body">
      <h4>Otoksen laajennus — erityislait</h4>
      <p>Tiedonhallintakartan piirissä olevat lait painottuvat hallinto-
      ja viranomaislainsäädäntöön. Kattavuuden parantamiseksi otosta
      laajennettiin keskeisillä erityislailla, joihin tunnetusti sisältyy
      runsaasti kieltoja, rangaistussäännöksiä ja muista oikeudenaloilta
      poikkeavia rakenteita:</p>
      <ul>
        <li><span class="pill" style="background:#7f8c8d">Rikos</span>
            <strong>{org_stats['RIKOS']['total']:,} pykälää</strong>
            (Rikoslaki, Pakkokeinolaki, Vankeuslaki, Ampuma-aselaki, ...)</li>
        <li><span class="pill" style="background:#7f8c8d">Vero</span>
            <strong>{org_stats['VERO']['total']:,} pykälää</strong>
            (Tuloverolaki, Arvonlisäverolaki, Verotusmenettelylaki, ...)</li>
        <li><span class="pill" style="background:#7f8c8d">Yksityis</span>
            <strong>{org_stats['YKSITYIS']['total']:,} pykälää</strong>
            (Avioliittolaki, Perintökaari, Maakaari, Kuluttajansuojalaki, ...)</li>
        <li><span class="pill" style="background:#7f8c8d">Yritys</span>
            <strong>{org_stats['YRITYS']['total']:,} pykälää</strong>
            (Osakeyhtiölaki, Asunto-osakeyhtiölaki, Konkurssilaki, ...)</li>
        <li><span class="pill" style="background:#7f8c8d">Työ</span>
            <strong>{org_stats['TYO']['total']:,} pykälää</strong>
            (Työsopimuslaki, Työturvallisuuslaki, Työtapaturmalaki)</li>
        <li><span class="pill" style="background:#7f8c8d">Hallinto</span>
            <strong>{org_stats['HALLINTO']['total']:,} pykälää</strong>
            (Hallintolaki, Oikeudenkäymiskaari, Kansalaisuuslaki, ...)</li>
        <li><span class="pill" style="background:#7f8c8d">Erikois</span>
            <strong>{org_stats['ERIKOIS']['total']:,} pykälää</strong>
            (Tekijänoikeuslaki, Patenttilaki, Ajokorttilaki, ...)</li>
      </ul>
      <p>Otos yhteensä: <strong>{all_stats['total']:,} pykälää</strong>
      ({n_laws} eri laista).</p>
    </div>
  </div>

  <hr class="divider">
  <h3>2. Luokittelu</h3>

  <div class="step">
    <div class="step-num">5</div>
    <div class="step-body">
      <h4>Kielimallipohjainen annotaatio (referenssi)</h4>
      <p>Jokainen pykälä luokiteltiin kielimallilla. Malli sai pykälän tekstin
      ja lain otsikon, ja palautti deonttisen luokan sekä lyhyen perustelun
      luonnollisella kielellä. Tätä annotaatiota käytetään referenssinä, johon
      sääntöpohjaista luokittelua verrataan.</p>
      <p>Käytetyt luokat:</p>
      <ul>
        <li><span class="pill" style="background:#2471a3">velvoite</span> — subjektilla on velvollisuus toimia</li>
        <li><span class="pill" style="background:#1e8449">lupa</span> — subjektilla on oikeus tai mahdollisuus toimia</li>
        <li><span class="pill" style="background:#c0392b">kielto</span> — toiminta on nimenomaisesti kielletty</li>
        <li><span class="pill" style="background:#d68910">suositus</span> — konditionaali tai pyrkiminen ilman ehdotonta velvoitetta</li>
        <li><span class="pill" style="background:#7f8c8d">ei_deontti</span> — määritelmä, voimaantulo tai muu ei-velvoittava sisältö</li>
      </ul>
    </div>
  </div>

  <div class="step">
    <div class="step-num">6</div>
    <div class="step-body">
      <h4>Sääntöpohjainen regex-luokitin</h4>
      <p>Kielimalliannotaatioiden perusteella tunnistettiin suomen
      lakikielelle tyypilliset deonttista modaliteettia ilmaisevat
      rakenteet. Näistä koostettiin viisitasoinen prioriteettijärjestelmä,
      jossa vahvimmat ja yksiselitteisimmät signaalit tunnistetaan ensin:</p>
      <ul>
        <li><strong>Taso 0 — rikoslakirakenteet:</strong>
            "Joka X, on tuomittava sakkoon/vankeuteen" -tyyppiset
            rangaistussäännökset luokitellaan kielloiksi (X on kielletty
            toiminta), vaikka rakenne sisältäisi velvoiteilmauksen
            tuomioistuimelle.</li>
        <li><strong>Taso 1 — vahvat passiivirakenteet:</strong>
            nesessitiivimuoto (<em>on tehtävä, on huolehdittava, on otettava</em>),
            eksplisiittiset velvoiteilmaukset (<em>velvoitetaan, on velvollinen</em>),
            ja eksplisiittiset kieltoilmaukset (<em>ei saa, kielletään, on kielletty</em>).</li>
        <li><strong>Taso 2 — ei-deonttiset ankkurit:</strong>
            voimaantulo, määritelmät ja soveltamisrajaukset
            (<em>tulee voimaan, tarkoitetaan, ei sovelleta</em>).</li>
        <li><strong>Taso 3 — modaaliset verbirakenteet:</strong>
            velvoitemodaalit infinitiivin kanssa (<em>tulee tehdä, pitää järjestää</em>),
            spesifit kiellot (<em>ei voida, ei myönnetä</em>),
            suositusrakenteet (<em>tulisi, olisi syytä, suositellaan</em>),
            sekä lupasignaalit (<em>voi, voidaan, saa, on oikeus</em>).</li>
        <li><strong>Taso 4 — aktiiviset toimivaltaverbit:</strong>
            organisaation toiminnan velvoittavuutta ilmaisevat verbit
            (<em>vastaa, valvoo, huolehtii, päättää, vahvistaa</em>).</li>
      </ul>
    </div>
  </div>

  <div class="step">
    <div class="step-num">7</div>
    <div class="step-body">
      <h4>Validointi ja iteratiivinen kehitys</h4>
      <p>Regex-luokittimen tarkkuutta arvioidaan vertaamalla sen tuotosta
      kielimalliannotaatioon. Virheanalyysin pohjalta säännöstöä on
      hiottu useassa kierroksessa: liian herkästi laukaisseet rakenteet
      tarkennettiin, ja prioriteettijärjestystä muokattiin niin että vahvat
      velvoittavat rakenteet voittavat määrittelyilmaukset silloin kun
      pykälässä esiintyy molempia.</p>
      <p>Tämänhetkinen kokonaistarkkuus on
      <strong>{pct(all_stats['acc'])}</strong> ({all_stats['total']:,}
      pykälän otoksella).</p>
    </div>
  </div>

  <hr class="divider">
  <h3>3. Tulosten tulkinta</h3>

  <p style="background:#fff3cd;padding:10px 14px;border-left:3px solid #d68910;border-radius:4px">
    <strong>Tärkeä huomio:</strong> Kumpaakaan menetelmää ei pidetä
    absoluuttisena totuutena. Tunnusluvut kuvaavat
    <em>menetelmien välistä yhtäpitävyyttä</em>, eivät kummankaan
    yksittäistä oikeellisuutta. Tämän työn aikana on havaittu tapauksia,
    joissa kielimalli on luokitellut esim. rikoslain rangaistussäännöksen
    velvoitteeksi (koska teksti sisältää passiivin nesessitiivimuodon
    <em>"on tuomittava sakkoon"</em>), kun taas sääntöperustainen luokitin
    on tunnistanut sen oikein kielloksi. Eroavat tapaukset kannattaa
    siksi tarkastella tapauskohtaisesti — ne paljastavat sekä regex-
    säännöstön rajoituksia että kielimallin omia tulkintavirheitä.
  </p>

  <p>Confusion matrix taulukon yläosassa näyttää, miten kahden menetelmän
  luokitukset jakautuvat. Diagonaalin solut kertovat tapaukset, joissa
  menetelmät päätyvät samaan luokkaan; muut solut paljastavat tyypilliset
  eroavaisuudet (esim. luokkien <em>lupa</em> ja <em>velvoite</em>
  rajatapaukset, joissa pykälä sisältää sekä mahdollistavan että
  velvoittavan rakenteen).</p>

  <p>Per-luokka-tilastoissa esitetään yhtäpitävyysmittarit, joissa
  kielimallin annotaatiota käytetään vertailupisteenä:</p>
  <ul>
    <li><strong>Precision</strong> — kun regex luokittelee tekstin luokkaan X,
        kuinka usein myös kielimalli päätyy samaan luokkaan</li>
    <li><strong>Recall</strong> — kun kielimalli luokittelee tekstin luokkaan X,
        kuinka usein myös regex päätyy samaan luokkaan</li>
    <li><strong>F1</strong> — Precisionin ja Recallin harmoninen keskiarvo;
        antaa yhden tunnusluvun molempien yhdenmukaisuudesta</li>
    <li><strong>TP / FN / FP</strong> — yhteneväiset luokitukset (TP),
        kielimallin luokitukset jotka regex tulkitsi toisin (FN),
        ja regexin luokitukset jotka kielimalli tulkitsi toisin (FP)</li>
  </ul>

  <p>Korkea F1 yhdessä luokassa tarkoittaa vahvaa yhdenmukaisuutta
  menetelmien välillä — se ei vielä todista kummankaan olevan
  absoluuttisesti oikeassa, mutta antaa luottamusta siihen, että
  kyseinen luokka on hyvin tunnistettavissa molemmilla tavoilla.</p>

  <hr class="divider">
  <h3>4. Menetelmän rajat</h3>

  <p>Sääntöpohjaisen regex-luokittimen tarkkuus jää lakitekstillä noin
  70 % tasolle. Pääsyy on lakikielen rakenteellinen monitulkintaisuus:
  yksittäinen pykälä voi sisältää sekä luvan että velvoitteen, ja se
  miten näitä painotetaan riippuu lauseyhteydestä. Sääntöperustainen
  luokitin valitsee aina ensimmäisen tunnistamansa signaalin, kun taas
  kielimalli pystyy painottamaan kontekstin perusteella.</p>

  <p>Tämä raportti dokumentoi kahden menetelmän vertailun. Korkeamman
  tarkkuuden saavuttaminen ilman ulkoista kielimalliriippuvuutta vaatii
  todennäköisesti suomenkielisen kielimallin (esim. FinBERT) ja sen
  pohjalle koulutetun keveän luokittelijan.</p>

</div>
</div><!-- /tab-info -->

<script>
const ROWS = {data_json};
const STATS = {stats_json};
const TOIMIJAT = {toimijat_json};
const COLORS = STATS.colors;

// ── Confusion matrix ──────────────────────────────────────────────────────────
(function buildConf() {{
  const cats = ["velvoite","lupa","kielto","suositus","ei_deontti"];
  const short = {{"velvoite":"velv","lupa":"lupa","kielto":"kiel","suositus":"suos","ei_deontti":"ei_d"}};
  const conf = STATS.conf;
  let html = '<table class="conf-tbl"><tr><th></th>';
  cats.forEach(c => html += `<th style="color:${{COLORS[c]}}">${{short[c]}}</th>`);
  html += '</tr>';
  cats.forEach(llm => {{
    html += `<tr><th style="color:${{COLORS[llm]}};text-align:left">${{short[llm]}}</th>`;
    cats.forEach(rx => {{
      const v = (conf[llm] && conf[llm][rx]) || 0;
      const isDiag = llm === rx;
      const bg = isDiag && v > 0
        ? `background:${{COLORS[llm]}}22`
        : (!isDiag && v > 50 ? 'background:#fff0f0' : '');
      html += `<td class="${{isDiag ? 'conf-diag' : ''}}" style="${{bg}}">${{v || ''}}</td>`;
    }});
    html += '</tr>';
  }});
  html += '</table>';
  document.getElementById('conf-table').innerHTML = html;
}})();

// ── Taulukko ──────────────────────────────────────────────────────────────────
let filtered = ROWS.slice();

function badge(cat) {{
  return `<span class="badge" style="background:${{COLORS[cat] || '#999'}}">${{cat}}</span>`;
}}

function renderRows(data) {{
  const tbody = document.getElementById('tbody');
  const PAGE = 500;
  let html = '';
  data.slice(0, PAGE).forEach(r => {{
    const cls = r.ok ? '' : 'mismatch';
    const icon = r.ok
      ? '<span class="match-icon" style="color:#1e8449">✓</span>'
      : '<span class="match-icon" style="color:#c0392b">✗</span>';
    html += `<tr class="${{cls}}">
      <td>${{icon}}</td>
      <td>${{badge(r.llm)}}</td>
      <td>${{badge(r.regex)}}</td>
      <td><span class="org-badge">${{r.org}}</span></td>
      <td>
        <div style="font-weight:500;font-size:12px">${{r.law}}</div>
        <div class="light" style="font-size:11px">${{r.num}}</div>
      </td>
      <td class="text-cell truncated">${{r.text}}</td>
    </tr>`;
  }});
  if (data.length > PAGE) {{
    html += `<tr><td colspan="6" style="text-align:center;padding:12px;color:#999">
      ... ${{data.length - PAGE}} riviä piilotettu — tarkenna suodattimia
    </td></tr>`;
  }}
  tbody.innerHTML = html;
  document.getElementById('count').textContent =
    `${{data.length.toLocaleString('fi')}} / ${{ROWS.length.toLocaleString('fi')}} riviä`;
}}

function applyFilters() {{
  const org    = document.getElementById('f-org').value;
  const llm    = document.getElementById('f-llm').value;
  const regex  = document.getElementById('f-regex').value;
  const errors = document.getElementById('f-errors').checked;
  const q      = document.getElementById('f-text').value.toLowerCase();

  filtered = ROWS.filter(r =>
    (!org    || r.org   === org)   &&
    (!llm    || r.llm   === llm)   &&
    (!regex  || r.regex === regex) &&
    (!errors || !r.ok)             &&
    (!q      || r.text.toLowerCase().includes(q) || r.law.toLowerCase().includes(q))
  );
  renderRows(filtered);
}}

applyFilters();

// ── Toimijat-välilehti ────────────────────────────────────────────────────────
let selectedToimija = null;

function renderToimijat() {{
  const orgFilter = document.getElementById('t-org').value;
  const modFilter = document.getElementById('t-mod').value;
  const q         = document.getElementById('t-search').value.toLowerCase();

  // Suodatetaan toimijat: laske uudelleen lukumäärät valitun org-tyypin / modaliteetin mukaan
  let list = TOIMIJAT.map(t => {{
    let rows = t.rows.map(i => ROWS[i]);
    if (orgFilter) rows = rows.filter(r => r.org === orgFilter);
    if (modFilter) rows = rows.filter(r => r.llm === modFilter);
    if (rows.length === 0) return null;
    const counts = {{velvoite:0, lupa:0, kielto:0, suositus:0, ei_deontti:0}};
    rows.forEach(r => counts[r.llm]++);
    return {{
      name: t.name, key: t.key, total: rows.length,
      rows: rows.map(r => r.i),
      ...counts
    }};
  }}).filter(t => t !== null);

  if (q) list = list.filter(t => t.name.toLowerCase().includes(q));
  list.sort((a, b) => b.total - a.total);

  document.getElementById('toimija-summary').textContent =
    `${{list.length.toLocaleString('fi')}} toimijaa, yhteensä ${{
      list.reduce((s, t) => s + t.total, 0).toLocaleString('fi')
    }} pykäläosumaa`;

  let html = '';
  list.slice(0, 300).forEach(t => {{
    const sel = (selectedToimija === t.key) ? 'selected' : '';
    const pill = (n, cat) => n > 0
      ? `<span class="t-count" style="background:${{COLORS[cat]}}">${{n}}</span>`
      : '<span class="light">·</span>';
    html += `<tr class="${{sel}}" onclick="selectToimija('${{t.key.replace(/'/g, "\\'")}}')">
      <td>${{t.name}}</td>
      <td class="num">${{t.total}}</td>
      <td class="num">${{pill(t.velvoite, 'velvoite')}}</td>
      <td class="num">${{pill(t.lupa, 'lupa')}}</td>
      <td class="num">${{pill(t.kielto, 'kielto')}}</td>
      <td class="num">${{pill(t.suositus, 'suositus')}}</td>
    </tr>`;
  }});
  if (list.length > 300) {{
    html += `<tr><td colspan="6" style="text-align:center;color:#999;padding:8px">
      ... ${{list.length - 300}} toimijaa piilotettu — tarkenna hakua
    </td></tr>`;
  }}
  document.getElementById('t-tbody').innerHTML = html;

  // Päivitä detail-paneli, jos valittu toimija on listassa
  if (selectedToimija) {{
    const t = list.find(x => x.key === selectedToimija);
    if (t) renderToimijaDetail(t);
  }}
}}

function selectToimija(key) {{
  selectedToimija = key;
  renderToimijat();
}}

function renderToimijaDetail(t) {{
  const detail = document.getElementById('toimija-detail');
  const orgFilter = document.getElementById('t-org').value;
  const modFilter = document.getElementById('t-mod').value;

  let rows = t.rows.map(i => ROWS[i]);

  // Järjestä: velvoite → lupa → kielto → suositus → ei_deontti
  const order = {{velvoite:0, lupa:1, kielto:2, suositus:3, ei_deontti:4}};
  rows.sort((a, b) => (order[a.llm] - order[b.llm]) || a.law.localeCompare(b.law));

  const summary = ['velvoite','lupa','kielto','suositus','ei_deontti']
    .filter(c => t[c] > 0)
    .map(c => `<span class="badge" style="background:${{COLORS[c]}}">${{c}} ${{t[c]}}</span>`)
    .join(' ');

  let html = `
    <h3>${{t.name}}</h3>
    <div class="summary">${{summary}}</div>
    <p class="light" style="margin-bottom:12px;font-size:12px">
      ${{t.total}} pykälää${{orgFilter ? ' · ' + orgFilter : ''}}${{modFilter ? ' · vain ' + modFilter : ''}}
    </p>`;

  rows.slice(0, 200).forEach(r => {{
    html += `<div class="pykala">
      <div class="pykala-header">
        ${{badge(r.llm)}}
        <span class="org-badge">${{r.org}}</span>
        <span style="font-weight:500">${{r.law}}</span>
        <span class="light">· ${{r.num}}</span>
      </div>
      <div class="pykala-text">${{r.text}}</div>
      ${{r.perust ? `<div class="perustelu">"${{r.perust}}"</div>` : ''}}
    </div>`;
  }});

  if (rows.length > 200) {{
    html += `<p class="light" style="text-align:center;padding:12px">
      ... ${{rows.length - 200}} pykälää piilotettu
    </p>`;
  }}

  detail.innerHTML = html;
}}

renderToimijat();

// ── Välilehtien vaihto ────────────────────────────────────────────────────────
function switchTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector(`[onclick="switchTab('${{name}}')"]`).classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}
</script>
</body>
</html>
"""

OUT_HTML.write_text(html, encoding="utf-8")
print(f"\nValmis: {OUT_HTML}")
