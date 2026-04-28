"""
Deonttinen regex-classifier suomenkieliselle lakitekstille.

Luokittelee jokaisen rivin modaliteettiin:
  velvoite, kielto, lupa, suositus, ei_deontti

Säännöt prioriteettijärjestyksessä — ensimmäinen osuma voittaa.
Rakennettu 20,000 LLM-annotoidun rivin pattern-analyysin pohjalta.

Käyttö:
    from deontic_classifier import classify, classify_df
"""
import re
import polars as pl

# ── Säännöt prioriteettijärjestyksessä ───────────────────────────────────────
# Järjestys on kriittinen — velvoite ennen kielto, jotta sekasisältöiset
# pykälät (velvoite + yksittäinen "ei saa") eivät luokitu virheellisesti kielloksi.

RULES: list[tuple[str, list[str]]] = [

    # TIER 0 — rikoslakikielto: pykälä alkaa "Joka X, on tuomittava ..."
    # Vaaditaan että teksti ALKAA sanalla "Joka" (kieltoehto kohdistuu
    # kuvattuun toimintaan). Tällä rajauksella säännöksen rakenne erottuu
    # esim. "joka jälkeen"/"joka tapauksessa" -lauseista, jotka eivät ole
    # rikoslakirakenteita.
    ("kielto", [
        # Teksti alkaa "Joka" ja sisältää tuomit + sakko/vankeut → rikoslakikielto
        r"^joka\b[\s\S]{0,500}\btuomit(?:taan|tava|tavaa)\b[\s\S]{0,200}\b(?:sakko\w*|vankeut\w*)\b",
        r"^joka\b[\s\S]{0,300}\brangaist(?:aan|a|us\w*)\b",
        # "rangaistaan sakolla/vankeudella" — passiivinen rangaistuskielto
        r"\brangaistaan\s+\w+(?:lla|llä)\b",
    ]),

    # TIER 1A — vahvat passiiviset velvoitesignaalit
    # Jos pykälässä on "on Xtava/tävä", se on velvoite vaikka kappale alkaisi
    # määrittelyllä. Yhdistetty -ttava ja -tava: kattaa myös "julkaistava",
    # "saatava", "annettava".
    ("velvoite", [
        r"\bon\b(?:\s+\S+){0,6}\s+\w+t(?:ava|ävä)\b",
        r"\bon\s+oltava\b",
        r"\bon\s+velvollinen\b",
        r"\bon\s+velvollisuus\b",
        r"\bvelvoitetaan\b",
        r"\bvaaditaan\b",
    ]),

    # TIER 1B — vahvat kieltosignaalit (eksplisiittiset)
    ("kielto", [
        r"\bei\s+saa\b",
        r"\bon\s+kielletty\b",
        r"\bon\s+kiellettyä\b",
        r"\bkielletään\b",
        r"\bälköön\b",
    ]),

    # TIER 2 — EI_DEONTTI: voimaantulo, määritelmät ja soveltamisalarajaukset
    ("ei_deontti", [
        r"tulee?\s+voimaan",
        r"tullessa\s+voimaan",
        r"voimaantul",
        r"tarkoitetaan",
        r"tarkoittaa\b",
        r"säädetään\s+erikseen",
        r"\bsovelletaan\s+(?:mitä|vastaavasti|tässä|myös|edelleen|kuitenkin|siten)\b",
        r"tässä\s+laissa\s+tarkoitet",
        r"tässä\s+momentissa\s+tarkoitet",
        r"kumotaan",
        r"muutetaan\s+seuraavasti",
        r"\bei\s+sovelleta\b",
    ]),

    # TIER 3A — velvoitemodaalit, vain kun seuraa infinitiivi
    # `tulee/pitää/täytyy` yksinään liian geneerisiä — vaadi vähintään 4-merkkinen
    # vokaaliin (a/ä) päättyvä sana (tyypillinen suomen kielen infinitiivipääte).
    # Kattaa: "tulee sopia/tehdä/järjestää/noudattaa/kiinnittää" jne.
    ("velvoite", [
        r"\btulee\s+\w{3,}[aä]\b",
        r"\btäytyy\s+\w{3,}[aä]\b",
        r"\bpitää\s+\w{3,}[aä]\b",
    ]),

    # TIER 3B — spesifit passiivikiellot
    ("kielto", [
        r"\bei\s+voida?\b",
        r"\bei\s+ole\s+oikeutta\b",
        r"\bei\s+ole\s+lupa\b",
        r"\bei\s+myönnetä\b",
        r"\bei\s+hyväksytä\b",
        r"\bei\s+suoriteta\b",
        r"\bei\s+luovuteta\b",
        r"\bei\s+anneta\b",
        r"\bei\s+makseta\b",
    ]),

    # TIER 3C — SUOSITUS (konditionaali ja pyrkiminen)
    ("suositus", [
        r"\btulisi\b",
        r"\bolisi\s+syytä\b",
        r"\bon\s+pyrittävä\b",
        r"\bon\s+vältettävä\b",
        r"\bon\s+harkittava\b",
        r"\bpyritään\b",
        r"\bsuositellaan\b",
        r"\bkehotetaan\b",
        r"tulee\s+ottaa\s+huomioon",
        r"tulee\s+pyrkiä",
    ]),

    # TIER 3D — LUPA (modaaliverbit)
    ("lupa", [
        r"\bvoi\b",
        r"\bvoidaan\b",
        r"\bvoivat\b",
        r"\bsaa\b",
        r"\bsaadaan\b",
        r"\bon\s+oikeus\b",
        r"\bon\s+toimivalta\b",
        r"\bon\s+oikeutettu\b",
        r"\bon\s+vapautettu\b",
        r"\bon\s+vapaa\b",
        r"\bei\s+tarvitse\b",
    ]),

    # TIER 4 — aktiiviset 3. persoonan velvoiteverbit (matalin prioriteetti)
    # Vain verbeja jotka tyypillisesti kuvaavat organisaation toimivaltaa
    # tai velvoittavaa toimintaa. Jätetty pois liian geneeriset (edistää, antaa,
    # turvaa) jotka esiintyvät usein myös puhtaasti kuvailevassa kontekstissa.
    ("velvoite", [
        r"\bvastaa\b",
        r"\bvastaavat\b",
        r"\bpäättää\b",
        r"\bpäättävät\b",
        r"\bmäärää\b",
        r"\bvahvistaa\b",
        r"\bnimittää\b",
        r"\bmyöntää\b",
        r"\bhuolehtii\b",
        r"\bhuolehtivat\b",
        r"\bvalvoo\b",
        r"\bvalvovat\b",
        r"\btekee\b",
        r"\btekevät\b",
        r"\bjärjestää\b",
        r"\bjärjestävät\b",
        r"\btoteuttaa\b",
        r"\blaatii\b",
        r"\bnoudattaa\b",
        r"\bvarmistaa\b",
        r"\bsuorittaa\b",
        r"\bmaksaa\b",
        r"\bilmoittaa\b",
        r"\bkäsittelee\b",
    ]),
]

# Esikäännetään regexpatternit suorituskyvyn parantamiseksi
_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (kat, [re.compile(p, re.IGNORECASE) for p in patterns])
    for kat, patterns in RULES
]


def classify(text: str) -> str:
    """
    Luokittelee yhden tekstin deonttiseen modaliteettiin.
    Palauttaa: 'velvoite' | 'kielto' | 'lupa' | 'suositus' | 'ei_deontti'
    """
    if not text or not text.strip():
        return "ei_deontti"

    t = text.lower()
    for modaliteetti, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(t):
                return modaliteetti

    return "ei_deontti"


def classify_df(df: pl.DataFrame, text_col: str = "text") -> pl.DataFrame:
    """
    Lisää modaliteetti-sarakkeen DataFrameen.
    Käyttää text_col-saraketta tai yhdistää intro+content jos text_col puuttuu.
    """
    if text_col not in df.columns:
        # Yhdistä intro ja content
        texts = [
            " ".join(filter(None, [row.get("intro", ""), row.get("content", "")])).strip()
            for row in df.iter_rows(named=True)
        ]
    else:
        texts = df[text_col].to_list()

    modaliteetit = [classify(t) for t in texts]
    return df.with_columns(pl.Series("modaliteetti", modaliteetit))


# ── Validointi LLM-annotointia vasten ────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    sample_csv = Path(__file__).resolve().parents[1] / "data" / "deontic_sample.csv"
    if not sample_csv.exists():
        sys.exit(f"Ei löydy: {sample_csv}")

    print("Ladataan LLM-annotoitu otos...")
    df = pl.read_csv(str(sample_csv), infer_schema_length=0)

    # Poistetaan LLM-virheet vertailusta
    df = df.filter(~pl.col("modaliteetti").is_in(["virhe", "ehto", "viittauslause"]))
    print(f"Rivejä vertailussa: {df.height:,}")

    # Ajetaan classifier
    texts = df["text"].to_list()
    regex_labels = [classify(t) for t in texts]
    df = df.with_columns(pl.Series("regex_modaliteetti", regex_labels))

    # Tarkkuus per luokka
    print("\n=== TARKKUUS PER LUOKKA ===")
    KATEGORIAT = ["velvoite", "kielto", "lupa", "suositus", "ei_deontti"]
    total_correct = 0
    total = len(df)

    for kat in KATEGORIAT:
        llm   = df.filter(pl.col("modaliteetti") == kat)
        if llm.is_empty():
            continue
        correct = llm.filter(pl.col("regex_modaliteetti") == kat).height
        pct = correct / llm.height * 100
        total_correct += correct
        print(f"  {kat:<12} LLM={llm.height:5d}  oikein={correct:5d}  tarkkuus={pct:.1f}%")

    overall = total_correct / total * 100
    print(f"\n  KOKONAISTARKKUUS: {overall:.1f}%  ({total_correct}/{total})")

    # Sekaannusmatriisi
    print("\n=== SEKAANNUKSET (regex -> LLM eri) ===")
    wrong = df.filter(pl.col("modaliteetti") != pl.col("regex_modaliteetti"))
    if not wrong.is_empty():
        conf = (
            wrong
            .group_by(["modaliteetti", "regex_modaliteetti"])
            .len()
            .sort("len", descending=True)
            .head(15)
        )
        print(f"  {'LLM':<14} {'REGEX':<14} {'kpl':>5}")
        print("  " + "-" * 36)
        for row in conf.iter_rows(named=True):
            print(f"  {row['modaliteetti']:<14} {row['regex_modaliteetti']:<14} {row['len']:>5}")
