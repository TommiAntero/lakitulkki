"""
Multi-label deonttinen luokittelu.

Käyttää samat regex-säännöt kuin pykälätason `deontic_classifier.py`, mutta
palauttaa yhden luokan sijaan **joukon** kaikista modaliteeteista jotka
tekstistä löytyvät.

Vertailtavaksi propositio-aineistoon (deontic_propositions.csv): jokaiselle
pykälälle voidaan laskea precision/recall/F1 per luokka.

Käyttö:
    from deontic_classifier_multilabel import classify_multilabel
    classify_multilabel("Hakemus on tehtävä määräajassa eikä siitä saa periä maksua.")
    # -> {"velvoite", "kielto"}
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deontic_classifier import RULES

# Esikäännetään patternit suorituskykyä varten
_COMPILED_RULES: list[tuple[str, list[re.Pattern]]] = [
    (cls, [re.compile(p, re.IGNORECASE) for p in patterns])
    for cls, patterns in RULES
]


# Negaation lookbehind-tarkistukset jälkikäsittelyä varten
_HAS_EI_SAA   = re.compile(r"\bei\s+saa\b",   re.IGNORECASE)
_HAS_EIKA     = re.compile(r"\beikä\b",        re.IGNORECASE)
_HAS_EI_VOI   = re.compile(r"\bei\s+voi\w*\b", re.IGNORECASE)


def classify_multilabel(text: str) -> set[str]:
    """
    Palauttaa kaikki modaliteetit jotka tekstistä löytyvät.

    Jos mikään deonttinen sääntö ei matchaa, palauttaa {"ei_deontti"}.
    Muutoin palauttaa kaikkien matchanneiden sääntöjen luokat.

    Pykälätason yksiluokkaisessa luokituksessa prioriteettijärjestys ratkaisi
    ristiriitatilanteet (esim. "ei saa" voitti "saa":n). Multi-labelissä
    prioriteettia ei ole, joten lisätään pieni jälkikäsittely jossa selvät
    ristiriidat puretaan negaation perusteella:

      - jos "ei saa" tekstissä → "lupa" (joka tuli pelkän "saa":n match-osumasta)
        poistetaan ja "kielto" lisätään
      - jos "eikä" tekstissä → kielto-signaali (yhdistetty negatiivinen
        konjunktio), lisätään "kielto"
      - jos "ei voi" tekstissä → "lupa" (joka tuli "voi/voidaan" -osumasta)
        poistetaan ja "kielto" lisätään
    """
    if not text or not text.strip():
        return {"ei_deontti"}

    t = text.lower()
    matched: set[str] = set()

    for cls, patterns in _COMPILED_RULES:
        for pat in patterns:
            if pat.search(t):
                matched.add(cls)
                break  # tämän luokan yhden osuman jälkeen voi siirtyä seuraavaan

    # ── Jälkikäsittely: negaation aiheuttamat lupa→kielto-konfliktit ─────────
    if _HAS_EI_SAA.search(t):
        matched.discard("lupa")
        matched.add("kielto")
    if _HAS_EI_VOI.search(t):
        matched.discard("lupa")
        matched.add("kielto")
    if _HAS_EIKA.search(t):
        # "eikä" merkitsee usein kieltoa ("eikä siitä saa periä"). Jos tekstissä
        # on samalla aikaa "saa"-osuma, se on todennäköisesti "eikä ... saa"
        # -negaation osa eikä erillinen lupa — poistetaan lupa.
        matched.add("kielto")
        if "saa" in t:
            matched.discard("lupa")

    if not matched:
        return {"ei_deontti"}
    return matched


# ── Sanity check / yksinkertainen testi ──────────────────────────────────────

if __name__ == "__main__":
    EXAMPLES = [
        ("Kunnan on järjestettävä asukkailleen riittävät sosiaalipalvelut.",
         {"velvoite"}),
        ("Hakemuksen voi tehdä sähköisesti tai kirjallisesti.",
         {"lupa"}),
        ("Asiakirjoja ei saa luovuttaa sivullisille.",
         {"kielto"}),
        ("Tässä laissa tarkoitetaan palkkatulolla...",
         {"ei_deontti"}),
        ("Hakemus on tehtävä määräajassa eikä siitä saa periä maksua.",
         {"velvoite", "kielto"}),
        ("Kunnan on järjestettävä palvelut, ja niitä saa antaa myös sähköisesti.",
         {"velvoite", "lupa"}),
        ("Tämä laki tulee voimaan 1 päivänä tammikuuta 2027.",
         {"ei_deontti"}),
    ]

    print("=== TESTITAPAUKSET ===\n")
    for text, expected in EXAMPLES:
        got = classify_multilabel(text)
        ok = "OK " if got == expected else "FAIL"
        print(f"  [{ok}] {text}")
        print(f"         odotettu: {expected}")
        print(f"         saatu:    {got}\n")
