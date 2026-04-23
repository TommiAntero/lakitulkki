"""
Finlex AKN XML -parseri.

Lukee yhden XML-tiedoston ja palauttaa polars DataFramen.
Yksi rivi per rakenneelementti (chapter, section, subsection,
paragraph, intro, crossHeading, annex).

Toimii rekursiivisesti — käy läpi minkä tahansa hierarkian
riippumatta siitä onko rakenne chapter→section, part→section
vai hcontainer→section.
"""
import hashlib
import re
from pathlib import Path

import polars as pl
from lxml import etree

AKN_NS    = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
FINLEX_NS = "http://data.finlex.fi/schema/finlex"

# Poimitaan vuosi ja numero href:stä
# /akn/fi/act/statute-consolidated/2011/806#chp_11__sec_5
_HREF_RE = re.compile(r"/akn/fi/act/[^/]+/(\d{4})/(\d+)(?:#(.+))?$")

NS = {
    "akn": AKN_NS,
    "fnx": FINLEX_NS,
}

# Rakenneelementit joista tehdään rivi
STRUCTURAL = {
    "chapter", "part", "section", "subsection",
    "paragraph", "intro", "crossHeading", "annex", "hcontainer",
}


# ── Apufunktiot: pienet tekstinpoiminta- ja hashfunktiot XML-elementeille ────

def _localname(el) -> str:
    return etree.QName(el.tag).localname


def _tag(name: str) -> str:
    return f"{{{AKN_NS}}}{name}"


def _all_text(el) -> str:
    """Koko elementtipuu tekstiksi."""
    if el is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(t.strip() for t in el.itertext() if t.strip())).strip()


def _child_text(el, tag_name: str) -> str:
    """Ensimmäisen suoran lapsielementin teksti."""
    child = el.find(_tag(tag_name))
    return _all_text(child) if child is not None else ""


def _intro_text(el) -> str:
    """<intro><p> -elementtien teksti (johdantolause ennen alakohtia)."""
    parts = []
    for child in el:
        if _localname(child) == "intro":
            for p in child:
                if _localname(p) == "p":
                    t = _all_text(p)
                    if t:
                        parts.append(t)
    return " ".join(parts)


def _content_text(el) -> str:
    """<content><p> -elementtien teksti."""
    parts = []
    for child in el:
        if _localname(child) == "content":
            for p in child:
                if _localname(p) == "p":
                    t = _all_text(p)
                    if t:
                        parts.append(t)
    return " ".join(parts)


def _extract_refs(el) -> str:
    """
    Poimii kaikki <ref>-elementit elementin sisältä.
    Palauttaa pipe-erotetun listan muodossa 'vuosi/numero#kohta'.
    Esim. '2003/434#sec_3 | 1982/710'
    """
    items = []
    for ref in el.iter(f"{{{AKN_NS}}}ref"):
        href = ref.get("href", "")
        m = _HREF_RE.search(href)
        if m:
            year, number, section = m.group(1), m.group(2), m.group(3) or ""
            entry = f"{year}/{number}"
            if section:
                entry += f"#{section}"
            items.append(entry)
    # Poistetaan duplikaatit säilyttäen järjestys
    seen = set()
    unique = [x for x in items if not (x in seen or seen.add(x))]
    return " | ".join(unique)


def _alakohdat(el) -> str:
    """
    Alakohdat (a), b), 1), 2)...) pipe-erotetuksi listaksi.
    Kattaa paragraph-, point- ja blockList/item-rakenteet.
    """
    items = []
    for child in el:
        local = _localname(child)
        if local in ("paragraph", "point"):
            num  = _child_text(child, "num")
            text = _content_text(child)
            if text:
                items.append(f"{num} {text}".strip() if num else text)
        elif local == "blockList":
            for item in child:
                if _localname(item) == "item":
                    num  = _child_text(item, "num")
                    text = _content_text(item)
                    if text:
                        items.append(f"{num} {text}".strip() if num else text)
    return " | ".join(items)


def _content_hash(content: str, intro: str = "") -> str:
    """SHA-256 (16 hex-merkkiä) muutostunnistusta varten."""
    combined = f"{intro} {content}".strip()
    if not combined:
        return ""
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


# ── Metatietojen poimiminen: hakee lain id:n, otsikon, voimassaolon ja version XML:n meta-osiosta ─

def _extract_law_id(root) -> str:
    """Poimii säädöstunnisteen muodossa 'numero_vuosi', esim. '1535_1992'."""
    uris = root.xpath(
        "//*[local-name()='FRBRWork']/*[local-name()='FRBRuri']/@value"
    )
    for uri in uris:
        parts = uri.strip("/").split("/")
        for i in range(len(parts) - 1):
            if parts[i].isdigit() and len(parts[i]) == 4:
                return f"{parts[i + 1]}_{parts[i]}"
    return "unknown"


def _extract_law_title(root) -> str:
    title = root.xpath(
        "string(//*[local-name()='preface']//*[local-name()='docTitle'][1])"
    )
    if title.strip():
        return re.sub(r"\s+", " ", title).strip().rstrip(".")
    title = root.xpath("string(//*[local-name()='FRBRname'][1]/@value)")
    return re.sub(r"\s+", " ", title).strip().rstrip(".")


def _is_in_force(root) -> str:
    """Palauttaa 'true'/'false'/'' riippuen fnx:isInForce-elementistä.
    Alkuperäisissä API-tiedostoissa kenttää ei ole — palautetaan ''."""
    el = root.find(".//akn:meta/akn:proprietary/fnx:isInForce", NS)
    return el.get("value", "") if el is not None else ""


def _extract_version(root) -> str:
    el = root.find(".//akn:FRBRExpression/akn:FRBRversionNumber", NS)
    return el.get("value", "") if el is not None else ""


# ── Rekursiivinen läpikäynti: kävelee XML-puun läpi ja kerää jokaisesta rakenneelementistä (section, subsection jne.) yhden rivin ─

def _walk(el, base: dict, parent_eid: str, depth: int, rows: list):
    """
    Kävelee XML-puun rekursiivisesti.
    Jokaisesta STRUCTURAL-elementistä tehdään yksi rivi.
    """
    local = _localname(el)

    if local in STRUCTURAL:
        eid     = el.get("eId") or el.get("id") or ""
        num     = _child_text(el, "num")
        heading = _child_text(el, "heading")
        intro   = _intro_text(el)
        content = _content_text(el)
        kohdat  = _alakohdat(el) if local in ("section", "subsection") else ""
        c_hash  = _content_hash(content, intro)
        refs    = _extract_refs(el)

        rows.append({
            **base,
            "eId":        eid,
            "type":       local,
            "num":        num,
            "heading":    heading,
            "intro":      intro,
            "content":    content,
            "alakohdat":  kohdat,
            "hash":       c_hash,
            "parent_eId": parent_eid,
            "depth":      depth,
            "has_ref":    refs != "",
            "refs":       refs,
        })

        next_parent = eid if eid else parent_eid
        next_depth  = depth + 1
    else:
        next_parent = parent_eid
        next_depth  = depth

    for child in el:
        _walk(child, base, next_parent, next_depth, rows)


# ── Fallback-parseri: varapolku kun _walk() ei löydä rakenteellisia elementtejä — hakee <p>-tagit suoraan XPathilla ─

def _parse_fallback(root, base: dict) -> list[dict]:
    """
    Varapolku: hakee kaikki <p>-elementit XPathilla ja lisää
    hierarkiakontekstin esi-isä-elementeistä.
    Käytetään kun _walk() ei löydä yhtään riviä.
    """
    NS = {"akn": AKN_NS}
    p_elements = root.xpath(".//akn:body//akn:p", namespaces=NS)
    rows = []

    for p in p_elements:
        text = _all_text(p)
        if not text:
            continue

        # Kerää hierarkia esi-isistä
        section_eid = ""
        parent_eid  = ""
        depth       = 1
        el_type     = "paragraph"

        ancestors = list(p.iterancestors())
        for anc in ancestors:
            local = _localname(anc)
            if local in STRUCTURAL:
                eid = anc.get("eId") or anc.get("id") or ""
                if not section_eid:
                    section_eid = eid
                    el_type     = local
                elif not parent_eid:
                    parent_eid = eid
                depth += 1

        refs   = _extract_refs(p)
        c_hash = _content_hash(text)

        rows.append({
            **base,
            "eId":        section_eid,
            "type":       el_type,
            "num":        "",
            "heading":    "",
            "intro":      "",
            "content":    text,
            "alakohdat":  "",
            "hash":       c_hash,
            "parent_eId": parent_eid,
            "depth":      depth,
            "has_ref":    refs != "",
            "refs":       refs,
            "parser_mode": "fallback",
        })

    return rows


# ── Julkinen rajapinta: parse_sections() on ainoa ulospäin kutsuttava funktio — palauttaa DataFramen yhdestä XML-tiedostosta ─

def parse_sections(xml_path: Path) -> pl.DataFrame:
    """
    Parsii yhden XML-tiedoston.
    Yrittää ensin omaa rekursiivista parseria (_walk).
    Jos ei löydä yhtään riviä, käyttää XPath-pohjaista fallbackia.
    Sarake parser_mode kertoo kumpaa käytettiin: 'main' tai 'fallback'.
    """
    root = etree.parse(str(xml_path), etree.XMLParser(recover=True)).getroot()

    law_id    = _extract_law_id(root)
    law_title = _extract_law_title(root)
    is_force  = _is_in_force(root)
    version   = _extract_version(root)

    base = {
        "law_id":      law_id,
        "law_title":   law_title,
        "isInForce":   is_force,
        "version":     version,
        "source_file": xml_path.name,
    }

    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        return pl.DataFrame()

    rows = []
    for child in body:
        _walk(child, base, law_id, 1, rows)

    if rows:
        for r in rows:
            r["parser_mode"] = "main"
    else:
        # Päälooginen parseri ei löytänyt mitään — kokeillaan fallbackia
        rows = _parse_fallback(root, base)

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows, schema_overrides={
        "depth": pl.Int32,
    })


def main():
    """Testiajo: parsii parser/input/inforce/ ja tulostaa näyte."""
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    in_dir = Path(__file__).parent / "input" / "inforce"
    xml_files = sorted(in_dir.glob("*.xml"))
    if not xml_files:
        raise SystemExit(f"Ei XML-tiedostoja: {in_dir}")

    frames = []
    for xml_path in xml_files[:50]:
        df = parse_sections(xml_path)
        if not df.is_empty():
            frames.append(df)

    combined = pl.concat(frames, how="diagonal")
    print(f"Rivejä: {combined.height}  sarakkeet: {combined.columns}")
    print(combined.head(10))


if __name__ == "__main__":
    main()
