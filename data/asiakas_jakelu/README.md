# Asiakkaalle jaettavat CSV-tiedostot

Tämä kansio sisältää keskeiset tulosCSV:t jotka voi lähettää asiakkaalle
demonstroimaan deonttista luokittelua ja propositio-tason ekstraktointia.

## Tiedostot

### `regex_propositions.csv` (15 MB)

77 288 deonttista propositiota muodossa `(toimija, modaliteetti, kohde)`,
ekstraktoituna paikallisesti regex-pohjaisesti **kaikkien 124 414 voimassa
olevan pykälän** aineistosta.

**Tuottanut:** `parser/proposition_extractor.py` (`--run-all`-tilassa)
**Sarakkeet:** law_id, law_title, eId, num, toimija, modaliteetti, kohde, distance

Tämä on se tulos jonka asiakkaan pitäisi saada kun he ajavat oman
Azure-pipelinensa läpi.

### `deontic_propositions.csv` (43 MB)

65 201 propositiota 16 114 pykälällä. Tuotettu **kielimallilla**
(Claude Haiku 4.5) tiedonhallintakartan piirissä olevien lakien sekä
keskeisten erityislakien osalta. Tämä on korkeampilaatuinen
**viitepohja**, johon regex-pohjaista ekstraktointia voi verrata.

**Sarakkeet:** law_id, eId, num, law_title, org_tyyppi, prop_id,
modaliteetti, toimija, kohde, type, perustelu, text, **tehtava**,
**toiminnan_kohde**

Toisessa vaiheessa `kohde`-kenttä on jaettu kahteen osaan kielimallin
avulla:
- `tehtava` — verbi/predikaatti (mitä toimitaan)
- `toiminnan_kohde` — substantiivilauseke (mihin toiminta kohdistuu)

Esimerkki: alkuperäinen `kohde="saada korvaus jälleenmyynnistä"` jakautuu
osiin `tehtava="saada"` ja `toiminnan_kohde="korvaus jälleenmyynnistä"`.

Keskimääräinen propositioiden määrä per pykälä:
- LLM: 4.05 (sisältää implisiittiset propositiot)
- Regex: 0.62 (vain selkeät rakenteet)

### `toimija_velvoitteet.csv` (10 MB)

LLM-aineistosta aggregoitu **toimija-keskeinen näkymä**: yksi rivi per
(toimija, modaliteetti) -pari. Lähde: `deontic_thk_sample.csv`.
Helpompi tutkia kysymyksiä kuten "mitkä velvoitteet kohdistuvat
hyvinvointialueisiin" tai "mitä kieltoja tekijään kohdistuu".

**Sarakkeet:** organisaatio, modaliteetti, org_tyyppi, law_title,
pykala, perustelu, teksti

### `consolidated_sections_lite.csv` (15 MB)

Yksi rivi per pykälä. Sisältää **regex-luokittimen pykälätason
modaliteetin** (yksi luokka per pykälä) kaikille 124 414 voimassa olevalle
pykälälle.

**Tuottanut:** `parser/deontic_classifier.py` -> `classify(text)`
**Sarakkeet:** law_id, law_title, eId, num, heading, modaliteetti_v4

Käytännöllinen pikaviite koko aineiston jakaumiin ja yhteenvetoihin.

## Modaliteettiluokat

| Luokka | Selitys |
|---|---|
| velvoite | Toimijalla on pakko tehdä jotain |
| lupa | Toimijalla on oikeus tai mahdollisuus toimia |
| kielto | Toiminta on nimenomaisesti kielletty |
| suositus | Konditionaali, pyrkiminen — ei pakottava |
| ei_deontti | Määritelmä, voimaantulo tai muu ei-deonttinen sisältö |

## Aineiston tausta

- Lähde: Finlexin avoin data, ajantasainen lainsäädäntö
- Parsittu: 124 414 pykälää 38 965 säädöstunnuksesta
- Aineiston päivitysajankohta: 2026-01-30 (Finlex-poiminta)
- Luokittelusääntöjen pohja: empiirinen pattern-louhinta + Lainkirjoittajan
  oppaan luvut 12.9 (rangaistussäännökset) ja 23 (määritelmät)

## Lisätietoja

Interaktiivinen raportti:
https://tommiantero.github.io/lakitulkki/data/deontic_report.html
