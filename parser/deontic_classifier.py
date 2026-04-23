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

    # 1. EI_DEONTTI — voimaantulo, määritelmät ja soveltamisalarajaukset ensin
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
        r"\bei\s+sovelleta\b",   # soveltamisalarajaus, ei varsinainen kielto
    ]),

    # 2. VELVOITE — nostettu ennen kielto: jos pykälässä on sekä velvoite-
    #    että kieltosignaali, velvoite voittaa (tyypillinen rakenne: velvoite
    #    pääsisältönä + poikkeusehto "ei saa" sivulauseessa)
    ("velvoite", [
        r"\bon\b(?:\s+\S+){0,4}\s+\w+ttava\b",
        r"\bon\b(?:\s+\S+){0,4}\s+\w+tävä\b",
        r"\bon\b(?:\s+\S+){0,4}\s+\w+ttavä\b",
        r"\btulee\b",
        r"\bpitää\b",
        r"\btäytyy\b",
        r"\bon\s+velvollisuus\b",
        r"\bvelvoitetaan\b",
        r"\bvaaditaan\b",
        r"\bon\s+velvollinen\b",
        r"\bon\s+oltava\b",
    ]),

    # 3. KIELTO — eksplisiittiset kiellot ja spesifit passiivikiellot
    #    Geneerinen \bei\s+\w+eta\b poistettu (aiheutti satoja FP pitkässä tekstissä)
    ("kielto", [
        r"\bei\s+saa\b",
        r"\bei\s+voida?\b",
        r"\bei\s+ole\s+oikeutta\b",
        r"\bei\s+ole\s+lupa\b",
        r"\bon\s+kielletty\b",
        r"\bkielletään\b",
        r"\bon\s+kiellettyä\b",
        r"\bälköön\b",
        r"\bei\s+myönnetä\b",
        r"\bei\s+hyväksytä\b",
        r"\bei\s+suoriteta\b",
        r"\bei\s+luovuteta\b",
        r"\bei\s+anneta\b",
        r"\bei\s+makseta\b",
    ]),

    # 4. SUOSITUS — konditionaali ja pyrkiminen
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

    # 5. LUPA — modaaliverbit
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

    # 6. VELVOITE — aktiiviset 3. persoonan velvoiteverbit (matala prioriteetti)
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
