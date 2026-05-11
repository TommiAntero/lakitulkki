# Asiakkaalle jaettavat CSV-tiedostot

Tämä kansio sisältää keskeiset tulosCSV:t jotka voi lähettää asiakkaalle
demonstroimaan deonttista luokittelua ja propositio-tason ekstraktointia.

## Tiedostot

### `regex_propositions.csv`

Deonttisia propositioita muodossa `(toimija, modaliteetti, kohde)`,
ekstraktoituna regex-pohjaisesti **kaikkien 153 052 pykälän** aineistosta.

**Tuottanut:** `parser/build_jakelu_from_momentit.py` (lukee
`momentit.csv` ja ajaa `proposition_extractor.extract_propositions`
jokaiselle pykälälle)
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

### `consolidated_sections_lite.csv`

Yksi rivi per pykälä. Sisältää **regex-luokittimen pykälätason
modaliteetin** (yksi luokka per pykälä) kaikille 153 052 pykälälle.

**Tuottanut:** `parser/build_jakelu_from_momentit.py`
(`deontic_classifier.classify(text)` per pykälä)
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
- Parsittu: 153 052 pykälää, 328 410 momenttia, 56 360 säädöstunnuksesta
- Aineiston päivitysajankohta: 2026-05-11 (Finlex-poiminta)
- Luokittelusääntöjen pohja: empiirinen pattern-louhinta + Lainkirjoittajan
  oppaan luvut 12.9 (rangaistussäännökset) ja 23 (määritelmät)

## Lisätietoja

Interaktiivinen raportti:
https://tommiantero.github.io/lakitulkki/data/deontic_report.html
