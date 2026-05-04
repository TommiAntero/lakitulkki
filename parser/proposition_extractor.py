"""
Paikallinen propositio-ekstraktori — regex-pohjainen vaihtoehto LLM-annotoinnille.

Tunnistaa pykälätekstistä (toimija, modaliteetti, kohde) -kolmikkoja
yhdistämällä toimija-mainintoja niiden lähellä esiintyviin
modaaliteettisignaaleihin.

Toimijalista on louhittu deontic_propositions.csv:stä (top-toimijat).
Modaaliteettisäännöt periytyvät V4-luokittimesta (deontic_classifier).

Käyttö koodista:
    from proposition_extractor import extract_propositions
    extract_propositions("Hyvinvointialueen on järjestettävä asukkailleen palvelut.")
    # -> [PropTriple(toimija="hyvinvointialue", modaliteetti="velvoite", kohde="...")]

Komentoriviltä:
    python parser/proposition_extractor.py             # itsetestit
    python parser/proposition_extractor.py --validate  # vertaa LLM-propositioihin
    python parser/proposition_extractor.py --run-all   # ajaa kaikille 124k pykälälle
"""
from __future__ import annotations
import re
import sys
import csv
import argparse
from dataclasses import dataclass
from pathlib import Path

# ── Toimijalista ──────────────────────────────────────────────────────────────
# Louhittu deontic_propositions.csv:n top-toimijoista. Kanonisoitu pienikirjaiminen
# muoto, jota regex-haku käyttää eri taivutusmuodoissa. Konsonanttigradaation
# (esim. kunta -> kunnan) huomioon ottavat vaihtoehdot listattu rinnan.

TOIMIJAT_LIST = [
    # canonical          stem-patterns (kaikki taivutusmuodot kattavat)
    ("viranomainen",     [r"viranomain", r"viranomaise"]),
    ("tuomioistuin",     [r"tuomioistuim", r"tuomioistuin"]),
    ("kunta",            [r"kunta", r"kunna", r"kuntien", r"kunnan"]),
    ("valtioneuvosto",   [r"valtioneuvosto", r"valtioneuvostolla", r"valtioneuvoston"]),
    ("hyvinvointialue",  [r"hyvinvointialue"]),
    ("asianosainen",     [r"asianosain", r"asianosaise"]),
    ("hakija",           [r"hakija"]),
    ("työnantaja",       [r"työnantaja", r"tyonantaja"]),
    ("yhtiö",            [r"yhtiö", r"yhtio"]),
    ("henkilö",          [r"henkilö", r"henkilo"]),
    ("valtio",           [r"valtio", r"valtion"]),
    ("vakuutusyhtiö",    [r"vakuutusyhtiö", r"vakuutusyhtio"]),
    ("toiminnanharjoittaja", [r"toiminnanharjoittaja"]),
    ("koulutuksen järjestäjä", [r"koulutuksen järjestäj", r"koulutuksen jarjestaj"]),
    ("valvontaviranomainen", [r"valvontaviranomain", r"valvontaviranomaise"]),
    ("verovelvollinen",  [r"verovelvollinen", r"verovelvollise"]),
    ("ulosottomies",     [r"ulosottomies", r"ulosottomiehe"]),
    ("hankintayksikkö",  [r"hankintayksikkö", r"hankintayksikko"]),
    ("poliisi",          [r"poliisi"]),
    ("myyjä",            [r"myyjä", r"myyja"]),
    ("osuuskunta",       [r"osuuskunta", r"osuuskunna"]),
    ("elinkeinonharjoittaja", [r"elinkeinonharjoittaja"]),
    ("ostaja",           [r"ostaja"]),
    ("työvoimaviranomainen", [r"työvoimaviranomain", r"tyovoimaviranomain"]),
    ("hallitus",         [r"hallitus", r"hallitukse"]),
    ("rekisteriviranomainen", [r"rekisteriviranomain", r"rekisteriviranomaise"]),
    ("käräjäoikeus",     [r"käräjäoikeu", r"karajaoikeu"]),
    ("yhtiökokous",      [r"yhtiökokou", r"yhtiokokou"]),
    ("velkoja",          [r"velkoja"]),
    ("vankila",          [r"vankila"]),
    ("luotonantaja",     [r"luotonantaja"]),
    ("jokainen",         [r"jokainen", r"jokaise"]),
    ("vakuutuksenantaja", [r"vakuutuksenantaja"]),
    ("hallintotuomioistuin", [r"hallintotuomioistuim"]),
    ("lupaviranomainen", [r"lupaviranomain", r"lupaviranomaise"]),
    ("pesänhoitaja",     [r"pesänhoitaja", r"pesanhoitaja"]),
    ("velallinen",       [r"velallin", r"velallise"]),
    ("vakuutuslaitos",   [r"vakuutuslaito"]),
    ("työntekijä",       [r"työntekijä", r"tyontekija"]),
    ("syyttäjä",         [r"syyttäjä", r"syyttaja"]),
    ("osakkeenomistaja", [r"osakkeenomistaja"]),
    ("toimija",          [r"toimija"]),
    ("teleyritys",       [r"teleyrity"]),
    ("kuluttaja",        [r"kuluttaja"]),
    ("luottolaitos",     [r"luottolaito"]),
    ("kirjanpitovelvollinen", [r"kirjanpitovelvollin", r"kirjanpitovelvollise"]),
    ("tekijä",           [r"tekijä", r"tekija"]),
    ("opiskelija",       [r"opiskelija"]),
    ("lapsi",            [r"lapsi", r"lapse"]),
    ("ministeriö",       [r"ministeriö", r"ministerio"]),
    ("oikeudenomistaja", [r"oikeudenomistaja"]),
    ("vuokraaja",        [r"vuokraaja"]),
    ("vuokranantaja",    [r"vuokranantaja"]),
    ("perillinen",       [r"perillin", r"perillise"]),
    ("testamentintekijä", [r"testamentintekijä", r"testamentintekija"]),
    ("aviopuoliso",      [r"aviopuoliso"]),
    ("vanhempi",         [r"vanhempi", r"vanhemma"]),
    ("kuluttaja-asiamies", [r"kuluttaja-asiamie"]),
    ("rikoksentekijä",   [r"rikoksentekijä", r"rikoksentekija"]),
    ("yrittäjä",         [r"yrittäjä", r"yrittaja"]),
    ("kuka tahansa",     [r"kuka tahansa", r"kenellä tahansa", r"kenelta tahansa"]),
    ("aluevaltuusto",    [r"aluevaltuusto", r"aluevaltuuston"]),
    ("aluehallitus",     [r"aluehallitus", r"aluehallitukse"]),
    ("eduskunta",        [r"eduskunta", r"eduskunnan"]),
    ("palveluntarjoaja", [r"palveluntarjoaja"]),
    ("palveluntuottaja", [r"palveluntuottaja"]),
    ("palvelunkäyttäjä", [r"palvelunkäyttäjä", r"palvelunkayttaja"]),
    ("perhehoitaja",     [r"perhehoitaja"]),
    ("lastenvalvoja",    [r"lastenvalvoja"]),
    ("oppilaitos",       [r"oppilaito"]),
    ("yliopisto",        [r"yliopisto", r"yliopiston"]),
    ("opettaja",         [r"opettaja"]),
    ("ehdokas",          [r"ehdokas", r"ehdokkaa"]),
    ("vaalipiirilautakunta", [r"vaalipiirilautakunna", r"vaalipiirilautakunta"]),
    ("kunnan keskusvaalilautakunta", [r"kunnan keskusvaalilautakunna", r"kunnan keskusvaalilautakunta"]),
    ("ahvenanmaan maakuntahallitus", [r"ahvenanmaan maakuntahallitukse", r"ahvenanmaan maakuntahallitus"]),
]

# Tunnetut organisaatiot (täsmällinen nimi, käytetään suoraan)
KNOWN_ORGS = [
    "liikenne- ja viestintävirasto",
    "lupa- ja valvontavirasto",
    "finanssivalvonta",
    "verohallinto",
    "elinvoimakeskus",
    "lääkealan turvallisuus- ja kehittämiskeskus",
    "ruokavirasto",
    "tulli",
    "kansaneläkelaitos",
    "kela",
    "terveyden ja hyvinvoinnin laitos",
    "thl",
    "sosiaali- ja terveysministeriö",
    "valtiokonttori",
    "valtiovarainministeriö",
    "sisäministeriö",
    "ympäristöministeriö",
    "opetus- ja kulttuuriministeriö",
    "puolustusvoimat",
    "rajavartiolaitos",
    "metsähallitus",
    "maanmittauslaitos",
    "patentti- ja rekisterihallitus",
    "kilpailu- ja kuluttajavirasto",
    "tietosuojavaltuutettu",
    "energiavirasto",
    "trafi",
    "ely-keskus",
    "avi",
]

# Yhdistetään regex: alterneeraus kaikista stem-patterneista
def _build_actor_regex():
    parts = []
    actor_map = []  # (kanoninen, paino) -- pidempi merkkijono ensin
    for canon, stems in TOIMIJAT_LIST:
        for s in stems:
            # kiinnitä sanaraja alkuun, salli mikä tahansa kirjainjatke
            if " " in s:
                # monisanaisille: salli loppuun \w*
                pat = rf"\b{re.escape(s)}\w*"
            else:
                pat = rf"\b{re.escape(s)}\w*\b"
            parts.append((pat, canon, len(s)))
    for org in KNOWN_ORGS:
        parts.append((rf"\b{re.escape(org)}\w*", org, len(org)))
    # Järjestä pisin pattern ensin → vältetään lyhyemmät osumat ennen pidempiä
    parts.sort(key=lambda x: -x[2])
    return parts

_ACTOR_RULES = _build_actor_regex()
_ACTOR_RE_PRE = [(re.compile(pat, re.IGNORECASE), canon) for pat, canon, _ in _ACTOR_RULES]

# Iso-alkukirjaiminen organisaationimi (kappaa Verohallinnon, Liikenne- ja viestintä..., jne.)
# Vältä yksittäisiä isokirjaimisia sanoja (lauseen alut)
_ORG_NAME_RE = re.compile(
    r"\b([A-ZÄÖ][a-zäö]+(?:[\- ]+(?:ja\s+)?[A-ZÄÖ][a-zäö]+)+)\b"
)


# ── Modaalisignaalit (kanttiväli huomioiva) ─────────────────────────────────

# Kuvaava lista: pattern → modaliteetti
MODAL_PATTERNS = [
    # Vahvat velvoite: passiivi nesessitiivi
    (r"\bon\b(?:\s+\S+){0,4}\s+\w+t(?:ava|ävä)\b", "velvoite"),
    (r"\bon\s+oltava\b",       "velvoite"),
    (r"\bon\s+velvollinen\b",  "velvoite"),
    (r"\bon\s+velvollisuus\b", "velvoite"),
    (r"\bvelvoitetaan\b",      "velvoite"),
    (r"\bvaaditaan\b",         "velvoite"),
    # Kielto: vahvat
    (r"\bei\s+saa\b",          "kielto"),
    (r"\bon\s+kielletty\b",    "kielto"),
    (r"\bon\s+kiellettyä\b",   "kielto"),
    (r"\bkielletään\b",        "kielto"),
    (r"\bälköön\b",            "kielto"),
    (r"\bei\s+voida?\b",       "kielto"),
    (r"\bei\s+ole\s+oikeutta\b", "kielto"),
    (r"\bei\s+ole\s+lupa\b",   "kielto"),
    (r"\bei\s+myönnetä\b",     "kielto"),
    (r"\bei\s+suoriteta\b",    "kielto"),
    (r"\bei\s+luovuteta\b",    "kielto"),
    (r"\bei\s+anneta\b",       "kielto"),
    (r"\bei\s+tuomita\b",      "kielto"),
    (r"\bei\s+rangaista\b",    "kielto"),
    # Velvoite: modaalit infinitiivin kanssa
    (r"\btulee\s+\w{3,}[aä]\b", "velvoite"),
    (r"\btäytyy\s+\w{3,}[aä]\b", "velvoite"),
    (r"\bpitää\s+\w{3,}[aä]\b", "velvoite"),
    # Suositus
    (r"\btulisi\b",            "suositus"),
    (r"\bolisi\s+syytä\b",     "suositus"),
    (r"\bon\s+pyrittävä\b",    "suositus"),
    (r"\bon\s+vältettävä\b",   "suositus"),
    (r"\bon\s+harkittava\b",   "suositus"),
    (r"\bsuositellaan\b",      "suositus"),
    (r"\bkehotetaan\b",        "suositus"),
    # Lupa
    (r"\bvoi\b",               "lupa"),
    (r"\bvoidaan\b",           "lupa"),
    (r"\bvoivat\b",            "lupa"),
    (r"\bsaa\b",               "lupa"),
    (r"\bsaadaan\b",           "lupa"),
    (r"\bon\s+oikeus\b",       "lupa"),
    (r"\bon\s+toimivalta\b",   "lupa"),
    (r"\bon\s+oikeutettu\b",   "lupa"),
    (r"\bon\s+vapautettu\b",   "lupa"),
    (r"\bei\s+tarvitse\b",     "lupa"),
    # Velvoite: aktiiviset toimivaltaverbit (matala prio: tulkitaan velvoitteeksi
    # vain jos toimija on selvästi lähellä)
    (r"\bvastaa\b",            "velvoite"),
    (r"\bpäättää\b",           "velvoite"),
    (r"\bmäärää\b",            "velvoite"),
    (r"\bhuolehtii\b",         "velvoite"),
    (r"\bvalvoo\b",            "velvoite"),
    (r"\btekee\b",             "velvoite"),
    (r"\bjärjestää\b",         "velvoite"),
    (r"\btoteuttaa\b",         "velvoite"),
    (r"\blaatii\b",            "velvoite"),
    (r"\bvarmistaa\b",         "velvoite"),
    (r"\bnoudattaa\b",         "velvoite"),
    (r"\bilmoittaa\b",         "velvoite"),
    (r"\bsuorittaa\b",         "velvoite"),
    (r"\bmaksaa\b",            "velvoite"),
    (r"\bkäsittelee\b",        "velvoite"),
    (r"\bvahvistaa\b",         "velvoite"),
    (r"\bnimittää\b",          "velvoite"),
    (r"\bmyöntää\b",           "velvoite"),
]

# Kompiloi nopeutta varten
_MODAL_RE_LIST = [(re.compile(p, re.IGNORECASE), m) for p, m in MODAL_PATTERNS]


# ── Datatyyppi ────────────────────────────────────────────────────────────────

@dataclass
class PropTriple:
    toimija: str
    modaliteetti: str
    kohde: str
    distance: int  # toimijan ja modaalin etäisyys merkeissä (debug/laatuarviointiin)


# ── Toimijahaun rakenne ───────────────────────────────────────────────────────

def find_actors(text: str) -> list[tuple[int, int, str, str]]:
    """Palauttaa lista (start, end, kanoninen_nimi, surface_form)."""
    found: list[tuple[int, int, str, str]] = []
    seen_spans: set[tuple[int, int]] = set()

    # Käy listapatternit pisimmästä lyhyimpaan, jotta "hyvinvointialue" ei
    # tule katetuksi lyhyemmällä "valtio"-osumalla
    for pat, canon in _ACTOR_RE_PRE:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            # Vältä päällekkäisyyttä: jos osa rajaa on jo katettu, ohita
            if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
                continue
            seen_spans.add(span)
            found.append((span[0], span[1], canon, m.group()))

    # Iso-alkukirjaiminen org-nimi (Verohallinto, Liikenne- ja viestintävirasto)
    for m in _ORG_NAME_RE.finditer(text):
        span = (m.start(), m.end())
        if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
            continue
        # Suodatetaan pois "Tämän lain" -tyyppiset (lauseen alut)
        if m.group().lower() in {"tämän lain", "tässä laissa", "sen estämättä",
                                  "siitä huolimatta", "tämän pykälän"}:
            continue
        seen_spans.add(span)
        found.append((span[0], span[1], m.group(), m.group()))

    found.sort()
    return found


def find_modals(text: str) -> list[tuple[int, int, str, str]]:
    """Palauttaa lista (start, end, modaliteetti, signaaliteksti)."""
    found: list[tuple[int, int, str, str]] = []
    for pat, mod in _MODAL_RE_LIST:
        for m in pat.finditer(text):
            found.append((m.start(), m.end(), mod, m.group()))
    found.sort()
    return found


# ── Pääfunktio: ekstraktoi propositiot ───────────────────────────────────────

def extract_propositions(text: str, max_distance: int = 200) -> list[PropTriple]:
    """
    Ekstraktoi (toimija, modaliteetti, kohde) -kolmikoita pykälätekstistä.

    Strategia: jokaiselle modaaliselle signaalille etsitään lähin **edeltävä**
    toimija (suomen kielen tyypillinen subjekti–verbi-järjestys). Jos toimijaa
    ei löydy lähistöltä, modaali jätetään sivuun (implisiittinen subjekti).
    """
    if not text or len(text) < 10:
        return []

    actors = find_actors(text)
    modals = find_modals(text)

    if not actors or not modals:
        return []

    # Negaatio-kohtelu (vrt. multilabel-luokitin)
    has_ei_saa = bool(re.search(r"\bei\s+saa\b", text, re.IGNORECASE))
    has_ei_voi = bool(re.search(r"\bei\s+voi\w*\b", text, re.IGNORECASE))
    has_eika   = bool(re.search(r"\beikä\b", text, re.IGNORECASE))

    triples: list[PropTriple] = []
    used_modal_idx: set[int] = set()

    for mi, (m_s, m_e, m_class, m_text) in enumerate(modals):
        # Negaatiokorjaus: jos "ei saa" tai "ei voi" tekstissä ja moodimainen
        # signaali on pelkkä "saa"/"voi", korjataan kielloksi
        signal = m_text.lower().strip()
        if signal in {"saa", "voi", "voidaan", "voivat", "saadaan"}:
            # Tarkista negaatio: jos "ei" tulee 0–6 merkin sisällä ennen
            preceding = text[max(0, m_s - 8):m_s].lower()
            if preceding.strip().endswith("ei") or "ei " in preceding[-6:]:
                m_class = "kielto"
            elif has_eika and signal == "saa":
                m_class = "kielto"

        # Etsi lähin edeltävä toimija (max_distance merkin sisällä)
        best_ai = None
        best_dist = max_distance
        for ai, (a_s, a_e, a_canon, a_surf) in enumerate(actors):
            if a_e <= m_s:
                dist = m_s - a_e
                # Varo lauseenrajaa (.) toimijan ja modaalin välissä
                between = text[a_e:m_s]
                penalty = between.count(".") * 80
                dist += penalty
                if dist < best_dist:
                    best_dist = dist
                    best_ai = ai
        if best_ai is None:
            continue

        # Poimi kohde: modaalin jälkeinen lauseen loppupätkä (max ~120 merkkiä)
        kohde_raw = text[m_e:m_e + 120]
        # Katkaise lauseen rajalle (. ; ?) — mukaan otetaan yksi lause
        m_end_period = re.search(r"[.;?]", kohde_raw)
        if m_end_period:
            kohde_raw = kohde_raw[:m_end_period.start()]
        kohde = kohde_raw.strip(" ,;:.")
        kohde = re.sub(r"\s+", " ", kohde)
        # Jätetään kohde tyhjäksi jos vain yhden sanan rippeitä
        if len(kohde) < 3:
            kohde = ""

        a_canon = actors[best_ai][2]
        triples.append(PropTriple(
            toimija=a_canon.lower(),
            modaliteetti=m_class,
            kohde=kohde[:200],
            distance=best_dist,
        ))
        used_modal_idx.add(mi)

    # Deduplikoi vain täydet duplikaatit (sama toimija + modaliteetti + kohde).
    # Sama (toimija, modaliteetti) eri kohteilla on eri propositio.
    seen: set[tuple[str, str, str]] = set()
    deduped: list[PropTriple] = []
    for t in triples:
        key = (t.toimija, t.modaliteetti, t.kohde[:50])  # kohde-fragmenttiin perustuva avain
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)
    return deduped


# ── Itsetestit ────────────────────────────────────────────────────────────────

TEST_CASES = [
    (
        "Hyvinvointialueen on järjestettävä asukkailleen riittävät sosiaalipalvelut.",
        [("hyvinvointialue", "velvoite")],
    ),
    (
        "Hakija voi tehdä hakemuksen sähköisesti tai kirjallisesti.",
        [("hakija", "lupa")],
    ),
    (
        "Asiakirjoja ei saa luovuttaa sivullisille.",
        [],  # ei toimijaa eksplisiittisesti
    ),
    (
        "Tekijällä on oikeus saada korvaus jälleenmyynnistä, mutta tekijä ei voi luovuttaa oikeutta kolmannelle.",
        [("tekijä", "lupa"), ("tekijä", "kielto")],
    ),
    (
        "Verohallinto määrää veron suuruuden ja se on suoritettava määräajassa.",
        [("verohallinto", "velvoite")],  # vähintään tämä; "se on suoritettava" voi tunnistua erikseen
    ),
]


def _selftest():
    print("=== ITSETESTIT ===\n")
    passed = 0
    for text, expected in TEST_CASES:
        result = extract_propositions(text)
        result_pairs = {(t.toimija, t.modaliteetti) for t in result}
        expected_pairs = set(expected)
        ok = expected_pairs.issubset(result_pairs)
        passed += ok
        print(f"  [{'OK ' if ok else 'FAIL'}]  {text}")
        for t in result:
            print(f"        ({t.toimija}, {t.modaliteetti}, '{t.kohde}', d={t.distance})")
        if not ok:
            print(f"        odotettu vähintään: {expected_pairs}")
        print()
    print(f"\nTuloksia: {passed}/{len(TEST_CASES)} itsetestiä läpäisi")


# ── Validointi LLM-aineistoa vasten ──────────────────────────────────────────

def _validate():
    """Vertaa ekstraktorin tulosta deontic_propositions.csv:hin.

    Käyttää sekä strict-vertailua (toimija exakti) että lenient-vertailua
    (substring-match toimijoiden välillä — koska LLM voi käyttää muotoa
    "kuvataiteen teoksen tekijä" kun regex tunnistaa kanonisen "tekijä").
    """
    import polars as pl

    ROOT = Path(__file__).resolve().parents[1]
    PROPS = ROOT / "data" / "deontic_propositions.csv"
    SAMPLE = ROOT / "data" / "deontic_thk_sample.csv"

    print("Luetaan vertailudata...")
    llm_props: dict[tuple, list[tuple[str, str]]] = {}
    with open(PROPS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["law_id"], r["eId"], r["num"])
            llm_props.setdefault(key, []).append(
                (r["toimija"].strip().lower(), r["modaliteetti"])
            )

    sample = pl.read_csv(str(SAMPLE), infer_schema_length=0)
    sample = sample.filter(~pl.col("modaliteetti").is_in(["virhe", "ehto", "viittauslause"]))

    print(f"Pykälää: {sample.height:,}")
    print(f"LLM-propositiopaikkoja: {sum(len(v) for v in llm_props.values()):,}")
    print()
    print("Ajetaan ekstraktori...")

    classes = ["velvoite", "lupa", "kielto", "suositus"]
    # Strict: exact toimija match
    s_tp = {c: 0 for c in classes}
    s_fp = {c: 0 for c in classes}
    s_fn = {c: 0 for c in classes}
    # Lenient: substring match
    l_tp = {c: 0 for c in classes}
    l_fp = {c: 0 for c in classes}
    l_fn = {c: 0 for c in classes}

    n = 0
    for row in sample.iter_rows(named=True):
        text = row["text"] or ""
        key = (row["law_id"], row["eId"], row["num"])
        rx_props = extract_propositions(text)
        rx_pairs_set = {(t.toimija, t.modaliteetti) for t in rx_props}
        llm_pairs_set = set(llm_props.get(key, []))

        # ── Strict (exact match) ──
        for c in classes:
            rx_c = {p for p in rx_pairs_set if p[1] == c}
            llm_c = {p for p in llm_pairs_set if p[1] == c}
            s_tp[c] += len(rx_c & llm_c)
            s_fp[c] += len(rx_c - llm_c)
            s_fn[c] += len(llm_c - rx_c)

        # ── Lenient (substring) ──
        for c in classes:
            rx_c = [p for p in rx_pairs_set if p[1] == c]
            llm_c = [p for p in llm_pairs_set if p[1] == c]
            rx_matched = set()
            llm_matched = set()
            for i, (rx_t, _) in enumerate(rx_c):
                for j, (llm_t, _) in enumerate(llm_c):
                    if j in llm_matched:
                        continue
                    if rx_t == llm_t or rx_t in llm_t or llm_t in rx_t:
                        rx_matched.add(i)
                        llm_matched.add(j)
                        break
            l_tp[c] += len(rx_matched)
            l_fp[c] += len(rx_c) - len(rx_matched)
            l_fn[c] += len(llm_c) - len(llm_matched)

        n += 1
        if n % 5000 == 0:
            print(f"  {n:,}/{sample.height:,}")

    def print_table(label, tp, fp, fn):
        print()
        print(f"=== {label} ===")
        print(f"  {'luokka':<10} {'TP':>6} {'FP':>6} {'FN':>6}  {'Prec':>6} {'Rec':>6} {'F1':>6}")
        ttp = tfp = tfn = 0
        for c in classes:
            p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0
            r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0
            f1 = 2 * p * r / (p + r) if (p + r) else 0
            ttp += tp[c]; tfp += fp[c]; tfn += fn[c]
            print(f"  {c:<10} {tp[c]:>6,} {fp[c]:>6,} {fn[c]:>6,}  {p*100:>5.1f}% {r*100:>5.1f}% {f1*100:>5.1f}%")
        p = ttp / (ttp + tfp) if (ttp + tfp) else 0
        r = ttp / (ttp + tfn) if (ttp + tfn) else 0
        f1 = 2 * p * r / (p + r) if (p + r) else 0
        print(f"  KOKONAIS  {ttp:>6,} {tfp:>6,} {tfn:>6,}  {p*100:>5.1f}% {r*100:>5.1f}% {f1*100:>5.1f}%")

    print_table("STRICT (exact toimija)", s_tp, s_fp, s_fn)
    print_table("LENIENT (substring toimija)", l_tp, l_fp, l_fn)


# ── Komentorivikäynnistys ────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true",
                        help="Vertaa LLM-propositioihin (deontic_propositions.csv)")
    parser.add_argument("--run-all", action="store_true",
                        help="Aja koko consolidated_sections.csv:lle ja tallenna tulos CSV:ksi")
    args = parser.parse_args()

    if args.validate:
        _validate()
    elif args.run_all:
        ROOT = Path(__file__).resolve().parents[1]
        IN = ROOT / "data" / "consolidated_sections.csv"
        OUT = ROOT / "data" / "regex_propositions.csv"
        print(f"Aja propositio-ekstraktori: {IN}")
        n_in = n_out = 0
        with open(IN, encoding="utf-8", newline="") as f, \
             open(OUT, "w", encoding="utf-8", newline="") as out:
            r = csv.DictReader(f)
            w = csv.writer(out)
            w.writerow(["law_id", "law_title", "eId", "num", "toimija",
                        "modaliteetti", "kohde", "distance"])
            for row in r:
                n_in += 1
                text = row.get("text") or ""
                if len(text) < 20:
                    continue
                triples = extract_propositions(text)
                for t in triples:
                    w.writerow([row["law_id"], row["law_title"], row["eId"], row["num"],
                                t.toimija, t.modaliteetti, t.kohde, t.distance])
                    n_out += 1
                if n_in % 10000 == 0:
                    print(f"  {n_in:,} pykälää, {n_out:,} propositiota")
        print(f"Valmis: {n_in:,} pykälää -> {n_out:,} propositiota")
        print(f"Output: {OUT}")
    else:
        _selftest()
