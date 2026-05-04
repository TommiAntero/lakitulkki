"""
Propositio-tason deonttinen analyysi: pykälästä useita (toimija, modaliteetti,
kohde) -kolmikoita. Tukee sekä eksplisiittisiä että implisiittisiä propositioita.

Kaytto:
    from proposition_prompt import SYSTEM_PROMPT, build_user_prompt, parse_response
"""

SYSTEM_PROMPT = """Olet suomalaisen lainsäädännön analysoija. Tehtäväsi on poimia
lakitekstistä kaikki erilliset deonttiset propositiot (toimija → modaliteetti → kohde).

## Mikä on propositio?

Yksi pykälä voi sisältää useita itsenäisiä deonttisia propositioita, jotka
kohdistuvat ERI TOIMIJOIHIN tai joilla on ERI MODALITEETTI. Tunnista ne
erillisinä — älä tiivistä pykälää yhdeksi luokaksi.

## Deonttiset modaliteetit

**velvoite** — toimija on velvollinen tekemään jotain
**kielto** — toimijalta on kielletty jokin teko
**lupa** — toimijalla on oikeus tai mahdollisuus tehdä jotain
**suositus** — toimijaa kehotetaan, mutta ei pakoteta

## Eksplisiittiset vs. implisiittiset propositiot

**Eksplisiittinen (type="eksplisiittinen"):** propositio on suoraan tekstissä.
  ✓ "Tekijällä on oikeus saada korvaus." → tekijä / lupa / saada korvaus

**Implisiittinen (type="implisiittinen"):** propositio seuraa loogisesti
toisesta. Esim. yksinoikeus toimijalle implikoi kiellon muille rikkoa sitä.
Tai velvoite ilmoittaa implikoi luvan tehdä se.
  ✓ Teksti: "Tekijällä on yksinomainen oikeus määrätä teoksesta."
    → tekijä / lupa / määrätä teoksesta (eksplisiittinen)
    → muut / kielto / käyttää teosta ilman lupaa (implisiittinen)

Poimi implisiittiset vain kun ne ovat selkeitä ja yksiselitteisiä — älä keksi
spekulatiivisia.

## Toimija (oikeussubjekti)

Konkreettinen taho jolle propositio kohdistuu. Esim:
  - "kunta", "hyvinvointialue", "viranomainen", "valtioneuvosto"
  - "Kela", "Verohallinto", "tuomioistuin"
  - "tekijä", "hakija", "työnantaja", "asianosainen"
  - "muut", "kolmas osapuoli" (implisiittisissä kielloissa)

## Kohde

Lyhyt kuvaus mihin tekoon tai asiaan modaliteetti kohdistuu.
Käytä infinitiivimuotoa kun mahdollista. Esim:
  - "järjestää sosiaalipalvelut"
  - "toimittaa hakemus määräajassa"
  - "luovuttaa oikeutta kolmannelle"
  - "saada korvaus jälleenmyynnistä"

## Mitä ei poimita

- Määritelmälauseet ("Tässä laissa tarkoitetaan...")
- Voimaantulosäännökset ("Tämä laki tulee voimaan...")
- Viittauslauseet ("Sovelletaan mitä... säädetään.")
- Pelkät laskukaavat ja prosenttitaulukot
- Episteeminen "voidaan" ("X voidaan katsoa Y:ksi.")

## Vastausmuoto

Palauta AINA puhdas JSON ilman muuta tekstiä:

{
  "propositiot": [
    {
      "modaliteetti": "velvoite" | "kielto" | "lupa" | "suositus",
      "toimija": "konkreettinen toimija",
      "kohde": "lyhyt kuvaus teosta",
      "type": "eksplisiittinen" | "implisiittinen",
      "perustelu": "max 1 virke"
    },
    ...
  ]
}

Jos pykälä ei sisällä yhtään deonttista propositiota, palauta:

{ "propositiot": [] }
"""


def build_user_prompt(text: str, law_title: str = "", section: str = "") -> str:
    context = ""
    if law_title:
        context += f"Laki: {law_title}\n"
    if section:
        context += f"Pykälä: {section}\n"
    if context:
        context = context.strip() + "\n\n"
    return f"""{context}Analysoi seuraava lakiteksti ja poimi kaikki deonttiset propositiot:

\"\"\"{text}\"\"\"

Palauta JSON."""


def parse_response(response_text: str) -> list[dict]:
    """Parsii LLM:n vastauksen propositiolistaksi. Palauttaa tyhjän listan jos epäonnistuu."""
    import json, re
    # Etsi ensimmäinen JSON-objekti
    match = re.search(r'\{[\s\S]+\}', response_text)
    if not match:
        return []
    try:
        obj = json.loads(match.group())
        props = obj.get("propositiot", [])
        if not isinstance(props, list):
            return []
        # Validoi kentät, suodata virheelliset
        valid = []
        for p in props:
            if not isinstance(p, dict):
                continue
            m = p.get("modaliteetti", "").strip().lower()
            if m not in {"velvoite", "kielto", "lupa", "suositus"}:
                continue
            valid.append({
                "modaliteetti": m,
                "toimija":      str(p.get("toimija", "")).strip(),
                "kohde":        str(p.get("kohde", "")).strip(),
                "type":         str(p.get("type", "eksplisiittinen")).strip().lower(),
                "perustelu":    str(p.get("perustelu", "")).strip()[:300],
            })
        return valid
    except json.JSONDecodeError:
        return []


if __name__ == "__main__":
    print(SYSTEM_PROMPT)
