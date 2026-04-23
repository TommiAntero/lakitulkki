"""
Rakentaa taulukkopohjaisen lakieksploraattorin.
Dropdown-valikosta valitaan laki, joka näyttää koko sivun levyisenä
taulukkona lain tiedot, sen viittaukset muihin lakeihin ja
mitkä lait viittaavat siihen.

Ei ulkoisia riippuvuuksia — toimii suoraan selaimessa.

Käyttö:
    python parser/build_law_explorer.py

Output: data/law_explorer.html
"""
import csv
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd

ROOT     = Path(__file__).resolve().parents[1]
IN_CSV   = ROOT / "data" / "consolidated_all.csv"
EXCEL    = ROOT / "Tiedonhallintakartta_tehtävät.xlsx"
OUT_HTML = ROOT / "data" / "law_explorer.html"

LAW_REF_RE = re.compile(r"\((\d+)/(\d{4})\)")

ORG_COLOR = {
    "HYVINVOINTIALUE": "#c0392b",
    "KUNTA":           "#2471a3",
    "VALTIO":          "#1e8449",
    "JAETTU":          "#7d3c98",
    "EI_THK":          "#7f8c8d",
}

# ── 1. THK-org-tyypit ─────────────────────────────────────────────────────────

print("Luetaan THK-Excel...")
sheets = pd.read_excel(str(EXCEL), sheet_name=None)
thk_orgs: dict[str, set] = defaultdict(set)
for sheet, df in sheets.items():
    if sheet not in ("HYVINVOINTIALUE", "KUNTA", "VALTIO"):
        continue
    col = "Tehtävän säädös ja lainkohta"
    if col not in df.columns:
        col = next((c for c in df.columns if "saad" in c.lower() or "laink" in c.lower()), None)
    if not col:
        continue
    for ref in df[col].dropna():
        m = LAW_REF_RE.search(str(ref))
        if m:
            thk_orgs[f"{m.group(1)}_{m.group(2)}"].add(sheet)

thk_ids = {
    lid: ("JAETTU" if len(orgs) > 1 else next(iter(orgs)))
    for lid, orgs in thk_orgs.items()
}
print(f"  THK-lakeja: {len(thk_ids):,}")

# ── 2. Lue CSV ────────────────────────────────────────────────────────────────

def eid_to_fi(eid: str) -> str:
    """Muuttaa eId-koodin luettavaksi pykäläviittaukseksi.
    Esim. 'chp_2__sec_8__subsec_1' → 'luku 2, § 8, mom. 1'
    """
    TYPE_MAP = {"chp": "luku", "sec": "§", "subsec": "mom.", "para": "kohta",
                "point": "kohta", "hcontainer": "osa", "part": "osa", "annex": "liite"}
    parts = eid.split("__")
    result = []
    for part in parts:
        idx = part.rfind("_")
        if idx > 0:
            typ = part[:idx]
            num = part[idx+1:]
            label = TYPE_MAP.get(typ, typ)
            result.append(f"{label} {num}")
    return ", ".join(result) if result else eid

print("Luetaan consolidated_all.csv...")
law_titles: dict[str, str] = {}
# adj_out[src][tgt] = set of section strings referenced
adj_out: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
adj_in:  dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))

with open(IN_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        src = row["law_id"]
        if src not in law_titles and row.get("law_title"):
            law_titles[src] = row["law_title"]
        for ref in row.get("refs", "").split(" | "):
            ref = ref.strip()
            if not ref:
                continue
            # Erottele laki ja pykälä: '2003/434#sec_3__subsec_2'
            if "#" in ref:
                law_part, sec_part = ref.split("#", 1)
                sec_label = eid_to_fi(sec_part)
            else:
                law_part = ref
                sec_label = ""
            parts = law_part.split("/")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                tgt = f"{parts[1]}_{parts[0]}"
                adj_out[src][tgt].add(sec_label)
                adj_in[tgt][src].add(sec_label)

print(f"  Lakeja: {len(law_titles):,}")

# ── 3. JSON-rakenne ───────────────────────────────────────────────────────────

print("Rakennetaan JSON...")

def law_label(lid: str) -> str:
    parts = lid.split("_")
    return f"{parts[0]}/{parts[1]}" if len(parts) == 2 else lid

# Mukaan vain THK-lait + niiden välittömät naapurit (1-hop)
relevant_ids: set[str] = set(thk_ids)
for lid in thk_ids:
    relevant_ids |= set(adj_out.get(lid, {}).keys())
    relevant_ids |= set(adj_in.get(lid, {}).keys())

print(f"  Relevantteja solmuja: {len(relevant_ids):,} (THK + 1-hop)")

nodes = {}
for lid in relevant_ids:
    org = thk_ids.get(lid, "EI_THK")
    nodes[lid] = {
        "l": law_label(lid),
        "t": law_titles.get(lid, ""),
        "o": org,
        "c": ORG_COLOR[org],
    }

def secs_label(secs: set) -> str:
    """Muodostaa pykälälistan: '§ 3, § 7' tai '' jos ei tunneta."""
    parts = sorted(s for s in secs if s)
    return ", ".join(parts) if parts else ""

# Viittaukset pykälätiedolla — lista {id, secs} -objekteja per laki
adj = {}
for lid in relevant_ids:
    out_entries = [
        {"id": tgt, "s": secs_label(secs)}
        for tgt, secs in adj_out.get(lid, {}).items()
        if tgt in relevant_ids
    ]
    in_entries = [
        {"id": src, "s": secs_label(secs)}
        for src, secs in adj_in.get(lid, {}).items()
        if src in relevant_ids
    ]
    if out_entries or in_entries:
        adj[lid] = {
            "o": sorted(out_entries, key=lambda x: x["id"]),
            "i": sorted(in_entries, key=lambda x: x["id"]),
        }

# Dropdown: THK-lait aakkosjärjestyksessä
dropdown = sorted(
    [{"id": lid, "label": f"{law_label(lid)} — {law_titles.get(lid, lid)}", "org": org}
     for lid, org in thk_ids.items()],
    key=lambda x: x["label"]
)

data_json = json.dumps(
    {"nodes": nodes, "adj": adj, "thk": thk_ids, "dropdown": dropdown, "colors": ORG_COLOR},
    ensure_ascii=False, separators=(",", ":")
)
print(f"  JSON: {len(data_json)//1024:,} KB")

# ── 4. HTML ───────────────────────────────────────────────────────────────────

html = f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<title>Lakieksploraattori</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: system-ui, -apple-system, sans-serif;
  background: #f5f6fa;
  color: #2d3436;
  height: 100vh;
  display: flex;
  flex-direction: column;
}}

/* ── Toolbar ── */
#toolbar {{
  padding: 10px 20px;
  background: #2d3436;
  color: #fff;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  flex-shrink: 0;
}}
#toolbar h1 {{ font-size: 15px; color: #b2bec3; white-space: nowrap; }}
#law-search {{
  flex: 1; min-width: 200px; max-width: 300px;
  padding: 6px 10px; border-radius: 4px;
  border: 1px solid #555; background: #3d3d3d;
  color: #fff; font-size: 13px;
}}
#law-select {{
  flex: 2; min-width: 300px;
  padding: 6px 10px; border-radius: 4px;
  border: 1px solid #555; background: #3d3d3d;
  color: #fff; font-size: 13px;
}}
#legend {{
  display: flex; gap: 12px; align-items: center; font-size: 12px;
  flex-wrap: wrap; color: #dfe6e9;
}}
.leg {{ display: flex; align-items: center; gap: 5px; }}
.leg-dot {{ width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }}

/* ── Sisältö ── */
#content {{
  flex: 1; overflow-y: auto; padding: 20px;
  display: flex; flex-direction: column; gap: 20px;
}}

/* ── Lain otsikkokortti ── */
#law-header {{
  background: #fff; border-radius: 8px;
  padding: 16px 20px; border-left: 5px solid #ccc;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  display: none;
}}
#law-header h2 {{ font-size: 18px; margin-bottom: 4px; }}
#law-header .meta {{ font-size: 13px; color: #636e72; }}

/* ── Taulukot ── */
.section-title {{
  font-size: 14px; font-weight: 600; color: #636e72;
  text-transform: uppercase; letter-spacing: 0.04em;
  margin-bottom: 8px;
}}
.ref-table {{
  width: 100%; border-collapse: collapse;
  background: #fff; border-radius: 8px; overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  font-size: 13px;
}}
.ref-table th {{
  background: #f0f0f0; padding: 9px 14px;
  text-align: left; font-weight: 600; font-size: 12px;
  color: #636e72; text-transform: uppercase; letter-spacing: 0.04em;
  border-bottom: 1px solid #dfe6e9;
}}
.ref-table td {{
  padding: 8px 14px; border-bottom: 1px solid #f0f0f0; vertical-align: top;
}}
.ref-table tr:last-child td {{ border-bottom: none; }}
.ref-table tr:hover td {{ background: #f8f9fa; cursor: pointer; }}
.badge {{
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600; color: #fff; white-space: nowrap;
}}
.law-id {{ font-family: monospace; color: #74b9ff; font-size: 12px; }}
.law-link {{ color: #0984e3; text-decoration: none; font-weight: 500; }}
.law-link:hover {{ text-decoration: underline; }}
#empty-msg {{
  text-align: center; color: #b2bec3; padding: 60px 0;
  font-size: 15px; display: block;
}}
.tables-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}}
@media (max-width: 900px) {{
  .tables-row {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<div id="toolbar">
  <h1>&#9906; Lakieksploraattori</h1>
  <input id="law-search" type="text" placeholder="Hae lakea..." oninput="filterDropdown()">
  <select id="law-select" onchange="selectLaw(this.value)">
    <option value="">— valitse laki —</option>
  </select>
  <div id="legend">
    <div class="leg"><div class="leg-dot" style="background:#c0392b"></div>Hyvinvointialue</div>
    <div class="leg"><div class="leg-dot" style="background:#2471a3"></div>Kunta</div>
    <div class="leg"><div class="leg-dot" style="background:#1e8449"></div>Valtio</div>
    <div class="leg"><div class="leg-dot" style="background:#7d3c98"></div>Jaettu</div>
    <div class="leg"><div class="leg-dot" style="background:#7f8c8d"></div>Muu</div>
  </div>
</div>

<div id="content">
  <span id="empty-msg">Valitse laki ylävalikosta</span>

  <div id="law-header">
    <h2 id="lh-title"></h2>
    <div class="meta">
      <span id="lh-id" class="law-id"></span> &nbsp;|&nbsp;
      <span id="lh-badge" class="badge"></span> &nbsp;|&nbsp;
      <span id="lh-counts"></span>
    </div>
  </div>

  <div class="tables-row" id="tables-row" style="display:none">
    <div>
      <div class="section-title" id="out-title">Viittaa muihin lakeihin</div>
      <table class="ref-table">
        <thead><tr>
          <th>Numero</th>
          <th>Laki</th>
          <th>Kohta</th>
          <th>Org-tyyppi</th>
        </tr></thead>
        <tbody id="out-body"></tbody>
      </table>
    </div>
    <div>
      <div class="section-title" id="in-title">Lait jotka viittaavat tähän</div>
      <table class="ref-table">
        <thead><tr>
          <th>Numero</th>
          <th>Laki</th>
          <th>Kohta</th>
          <th>Org-tyyppi</th>
        </tr></thead>
        <tbody id="in-body"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const DATA = {data_json};

const ORG_LABELS = {{
  HYVINVOINTIALUE: "Hyvinvointialue",
  KUNTA: "Kunta",
  VALTIO: "Valtio",
  JAETTU: "Jaettu",
  EI_THK: "—",
}};

function nLabel(id) {{ const n = DATA.nodes[id]; return n ? n.l : id; }}
function nTitle(id) {{ const n = DATA.nodes[id]; return n ? n.t : ""; }}
function nOrg(id)   {{ const n = DATA.nodes[id]; return n ? n.o : "EI_THK"; }}
function nColor(id) {{ const n = DATA.nodes[id]; return n ? n.c : "#7f8c8d"; }}

let allOptions = DATA.dropdown.slice();

function renderDropdown(opts) {{
  const sel = document.getElementById("law-select");
  const cur = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  opts.forEach(o => {{
    const opt = document.createElement("option");
    opt.value = o.id;
    opt.textContent = o.label;
    sel.appendChild(opt);
  }});
  if (cur && opts.find(o => o.id === cur)) sel.value = cur;
}}

function filterDropdown() {{
  const q = document.getElementById("law-search").value.toLowerCase();
  renderDropdown(q ? allOptions.filter(o => o.label.toLowerCase().includes(q)) : allOptions);
}}

function makeRow(entry) {{
  const id = entry.id;
  const sec = entry.s || "—";
  const org = nOrg(id);
  const color = nColor(id);
  const label = ORG_LABELS[org] || "—";
  return `<tr onclick="selectLaw('${{id}}')" title="Valitse tämä laki">
    <td><span class="law-id">${{nLabel(id)}}</span></td>
    <td><a class="law-link">${{nTitle(id) || id}}</a></td>
    <td style="color:#555;font-size:12px">${{sec}}</td>
    <td><span class="badge" style="background:${{color}}">${{label}}</span></td>
  </tr>`;
}}

function selectLaw(lawId) {{
  if (!lawId) return;
  document.getElementById("law-select").value = lawId;
  document.getElementById("empty-msg").style.display = "none";

  const adj = DATA.adj[lawId] || {{}};
  const out = adj.o || [];
  const inn = adj.i || [];
  const org = nOrg(lawId);

  // Otsikkokortti
  const header = document.getElementById("law-header");
  header.style.display = "block";
  header.style.borderLeftColor = nColor(lawId);
  document.getElementById("lh-title").textContent = nTitle(lawId) || lawId;
  document.getElementById("lh-id").textContent = lawId;
  const badge = document.getElementById("lh-badge");
  badge.textContent = ORG_LABELS[org] || org;
  badge.style.background = nColor(lawId);
  document.getElementById("lh-counts").textContent =
    `Viittaa ${{out.length}} lakiin · ${{inn.length}} lakia viittaa tähän`;

  // Taulukot
  document.getElementById("tables-row").style.display = "grid";
  document.getElementById("out-title").textContent = `Viittaa muihin lakeihin (${{out.length}})`;
  document.getElementById("in-title").textContent  = `Lait jotka viittaavat tähän (${{inn.length}})`;

  const emptyRow = "<tr><td colspan='4' style='color:#aaa;padding:16px'>Ei viittauksia</td></tr>";
  document.getElementById("out-body").innerHTML = out.length ? out.map(makeRow).join("") : emptyRow;
  document.getElementById("in-body").innerHTML  = inn.length ? inn.map(makeRow).join("") : emptyRow;
}}

renderDropdown(allOptions);
</script>
</body>
</html>
"""

OUT_HTML.write_text(html, encoding="utf-8")
print(f"\nValmis: {OUT_HTML}")
