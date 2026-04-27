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
ORGS = ["HYVINVOINTIALUE", "KUNTA", "VALTIO"]

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
            "llm":   llm,
            "regex": regex,
            "org":   org,
            "law":   row.get("law_title", "")[:60],
            "num":   row.get("num", "") or row.get("eId", ""),
            "text":  text[:400],
            "ok":    llm == regex,
        })

n_laws = len(law_ids - {""})
print(f"  Rivejä: {len(rows):,}")
print(f"  Lakeja: {n_laws}")

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

<!-- ── INFO-VÄLILEHTI ── -->
<div id="tab-info" class="tab-content">
<div id="info-content">

  <h2>Deonttinen analyysi — menetelmä ja aineisto</h2>
  <p>Tämä työkalu luokittelee Suomen voimassa olevien lakien pykälät
  deonttisen modaliteetin mukaan: <em>velvoite, lupa, kielto, suositus</em> tai
  <em>ei-deontti</em>. Raportti vertaa kahta luokittelumenetelmää:
  kielimalliperustaista (LLM) annotaatiota ja sääntöperustaista
  (regex) luokitinta. Tarkoituksena on tuottaa skaalautuva ja paikallisesti
  ajettava menetelmä lakitekstin velvoittavuusrakenteen analyysiin.</p>

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
      <h4>Otoksen rajaus tiedonhallintakartan mukaan</h4>
      <p>Analyysiin valittiin lait, jotka esiintyvät julkishallinnon
      tiedonhallintakartassa. Lait ryhmiteltiin organisaatiotyypeittäin:</p>
      <ul>
        <li><span class="pill" style="background:#c0392b">Hyvinvointialue</span>
            <strong>{org_stats['HYVINVOINTIALUE']['total']:,} pykälää</strong> (kaikki mukaan)</li>
        <li><span class="pill" style="background:#2471a3">Kunta</span>
            <strong>{org_stats['KUNTA']['total']:,} pykälää</strong> (satunnaisotos)</li>
        <li><span class="pill" style="background:#1e8449">Valtio</span>
            <strong>{org_stats['VALTIO']['total']:,} pykälää</strong> (satunnaisotos)</li>
      </ul>
      <p>Otos yhteensä: <strong>{all_stats['total']:,} pykälää</strong>
      ({n_laws} eri laista).</p>
    </div>
  </div>

  <hr class="divider">
  <h3>2. Luokittelu</h3>

  <div class="step">
    <div class="step-num">4</div>
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
    <div class="step-num">5</div>
    <div class="step-body">
      <h4>Sääntöpohjainen regex-luokitin</h4>
      <p>Kielimalliannotaatioiden perusteella tunnistettiin suomen
      lakikielelle tyypilliset deonttista modaliteettia ilmaisevat
      rakenteet. Näistä koostettiin neljätasoinen prioriteettijärjestelmä,
      jossa vahvimmat ja yksiselitteisimmät signaalit tunnistetaan ensin:</p>
      <ul>
        <li><strong>Taso 1 — vahvat passiivirakenteet:</strong>
            nesessitiivimuoto (<em>on tehtävä, on huolehdittava, on otettava</em>),
            sekä eksplisiittiset velvoite- ja kieltoilmaukset
            (<em>velvoitetaan, ei saa, kielletään</em>).</li>
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
    <div class="step-num">6</div>
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

  <p>Confusion matrix taulukon yläosassa näyttää, miten luokat sekoittuvat
  toisiinsa. Diagonaalin solut kertovat oikein luokitellut tapaukset; muut
  solut paljastavat tyypilliset virhetyypit (esim. luokkien <em>lupa</em> ja
  <em>velvoite</em> rajatapaukset, joissa pykälä sisältää sekä mahdollistavan
  että velvoittavan rakenteen).</p>

  <p>Per-luokka-tilastoissa esitetään:</p>
  <ul>
    <li><strong>Precision</strong> — kuinka usein regex on oikeassa, kun se
        antaa kyseisen luokan</li>
    <li><strong>Recall</strong> — kuinka iso osa kyseisen luokan
        tapauksista regex tunnistaa</li>
    <li><strong>F1</strong> — Precisionin ja Recallin harmoninen keskiarvo</li>
    <li><strong>TP/FN/FP</strong> — oikeat osumat / vääriksi negatiiviksi
        jääneet / vääriksi positiivisiksi luokitellut</li>
  </ul>

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
