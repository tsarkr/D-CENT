"""Step 8: add name readings to graph nodes: romanized variants and Hangul readings.

1. Hangul readings for Place and Organization nodes whose name is written in
   Hanja and that carry no reading yet (e.g. 寬勳洞 -> 관훈동), so that the graph
   view can label them "寬勳洞 (관훈동)" as it does for persons. Readings use the
   same conversion as the TEI ruby annotation (pipeline.text).
2. Romanized variants of person names for the full-text index (below).
3. Labels of Activity nodes that state what they are: the mention of a person
   in one record, in order of mention ("유관순 — 출처정보 <출처정보아이디> 언급 (8번째)"; "#<rowid>" when
   the record has no source identifier).
   They do not represent an attested action, date, or place.

Questions in English often spell Korean names in different systems
(Lee/Yi/Rhee Dong-hwi, Yu Gwan-sun/Ryu Kwan-sun). Query analysis converts
such names into Hangul with the language model; this step gives the
full-text index a deterministic fallback that does not depend on that call.

For every Person node whose Hangul name (한글명칭 or 명칭) has two to four
syllables, the property ``romanized`` is set to a list of variants:

  * the surname in Revised Romanization and its common historical spellings
    (Lee, Yi, Rhee, I; Park, Pak, Bak; ...);
  * the given name in Revised Romanization and in a McCune-Reischauer-like
    form (initial g/d/b/j -> k/t/p/ch, eo -> o, eu -> u), each with and
    without a hyphen between syllables.

The ``namesIndex`` full-text index is extended with ``romanized`` if it does
not already cover it. Source properties are not changed.

Run as part of `python -m pipeline build graph`.
"""

from __future__ import annotations

import re


INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
MEDIALS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi",
           "yu", "eu", "ui", "i"]
FINALS = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t",
          "ng", "t", "t", "k", "t", "p", "t"]

COMPOUND_SURNAMES = {"남궁", "황보", "제갈", "선우", "독고", "사공", "서문", "동방"}

# Common historical and personal spellings of surnames (first entry need not be RR).
SURNAME_VARIANTS = {
    "이": ["Yi", "Lee", "Rhee", "I", "Ri"], "김": ["Kim", "Gim"], "박": ["Park", "Pak", "Bak"],
    "최": ["Choi", "Choe"], "정": ["Jeong", "Chung", "Jung", "Chong"], "강": ["Kang", "Gang"],
    "조": ["Jo", "Cho", "Joh"], "윤": ["Yun", "Yoon"], "장": ["Jang", "Chang"], "임": ["Im", "Lim", "Rim"],
    "한": ["Han"], "오": ["O", "Oh"], "서": ["Seo", "Suh", "So"], "신": ["Sin", "Shin"],
    "권": ["Gwon", "Kwon"], "황": ["Hwang"], "안": ["An", "Ahn"], "송": ["Song"], "홍": ["Hong"],
    "전": ["Jeon", "Chun", "Jun", "Chon"], "고": ["Go", "Ko"], "문": ["Mun", "Moon"], "손": ["Son", "Sohn"],
    "백": ["Baek", "Paik", "Paek"], "허": ["Heo", "Hur", "Huh"], "남": ["Nam"], "노": ["No", "Noh", "Roh"],
    "하": ["Ha"], "곽": ["Gwak", "Kwak"], "성": ["Seong", "Sung"], "차": ["Cha"], "주": ["Ju", "Joo", "Chu"],
    "우": ["U", "Woo"], "구": ["Gu", "Koo", "Ku"], "민": ["Min"], "유": ["Yu", "Yoo", "Ryu", "You"],
    "류": ["Ryu", "Yu", "Yoo"], "나": ["Na", "Ra"], "진": ["Jin", "Chin"], "지": ["Ji", "Chi"],
    "엄": ["Eom", "Um", "Uhm"], "채": ["Chae"], "원": ["Won"], "천": ["Cheon", "Chun"], "방": ["Bang", "Pang"],
    "양": ["Yang"], "현": ["Hyeon", "Hyun"], "변": ["Byeon", "Byun", "Pyon"], "염": ["Yeom", "Yum"],
    "여": ["Yeo", "Yo"], "추": ["Chu"], "도": ["Do", "Doh"], "소": ["So"], "석": ["Seok", "Suk"],
    "설": ["Seol", "Sul"], "마": ["Ma"], "길": ["Gil", "Kil"], "연": ["Yeon", "Yun"], "위": ["Wi"],
    "표": ["Pyo"], "명": ["Myeong", "Myung"], "기": ["Gi", "Ki"], "반": ["Ban", "Pan"], "왕": ["Wang"],
    "금": ["Geum", "Keum"], "옥": ["Ok"], "육": ["Yuk"], "인": ["In"], "맹": ["Maeng"], "제": ["Je"],
    "모": ["Mo"], "탁": ["Tak"], "국": ["Guk", "Kook"], "어": ["Eo"], "은": ["Eun"], "편": ["Pyeon"],
    "용": ["Yong"], "예": ["Ye"], "경": ["Gyeong", "Kyung"], "봉": ["Bong"], "사": ["Sa"], "부": ["Bu", "Boo"],
    "황보": ["Hwangbo"], "남궁": ["Namgung", "Namkung"], "제갈": ["Jegal"], "선우": ["Seonu", "Sunwoo"],
    "독고": ["Dokgo"], "사공": ["Sagong"], "서문": ["Seomun"], "동방": ["Dongbang"],
}

_MR_INITIAL = {"g": "k", "d": "t", "b": "p", "j": "ch"}
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def hanja_reading(name: str) -> str | None:
    """Hangul reading of a Hanja name, or None if it has no Hanja or cannot be read fully."""
    name = (name or "").strip()
    if not name or not _HAN.search(name):
        return None
    from pipeline.text import translate_hanja_name

    reading = translate_hanja_name(name).strip()
    return reading if reading and not _HAN.search(reading) else None


def _syllables(text: str) -> list[tuple[str, str, str]] | None:
    parts = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if not 0 <= code <= 11171:
            return None
        parts.append((INITIALS[code // 588], MEDIALS[(code % 588) // 28], FINALS[code % 28]))
    return parts


def _rr(syllable: tuple[str, str, str]) -> str:
    return "".join(syllable)


def _mr_like(syllable: tuple[str, str, str]) -> str:
    initial, medial, final = syllable
    initial = _MR_INITIAL.get(initial, initial)
    medial = medial.replace("eo", "o").replace("eu", "u")
    return initial + medial + final


def romanize_name(name: str) -> list[str]:
    """Return romanized variants of a Korean personal name (empty if not 2-4 Hangul syllables)."""
    name = (name or "").strip()
    if not re.fullmatch(r"[가-힣]{2,4}", name):
        return []
    surname = name[:2] if name[:2] in COMPOUND_SURNAMES and len(name) >= 3 else name[:1]
    given = name[len(surname):]
    sur_syl, given_syl = _syllables(surname), _syllables(given)
    if not sur_syl or not given_syl:
        return []
    surnames = SURNAME_VARIANTS.get(surname) or ["".join(_rr(s) for s in sur_syl).capitalize()]
    given_forms = []
    for convert in (_rr, _mr_like):
        pieces = [convert(s) for s in given_syl]
        given_forms.append("-".join(pieces).capitalize())   # Dong-hwi
        given_forms.append("".join(pieces).capitalize())    # Donghwi
    variants = []
    for sur in surnames:
        for given_form in given_forms:
            variant = f"{sur} {given_form}"
            if variant not in variants:
                variants.append(variant)
    return variants


MENTION_LABEL_QUERY = """
MATCH (a:Activity)
OPTIONAL MATCH (a)-[:P14_carried_out_by]->(p:Person)
WITH a, head(collect(coalesce(p.한글명칭, p.명칭))) AS actor,
     toInteger(last(split(a.uid, '_'))) + 1 AS ord,
     CASE a.source_table
       WHEN 'raw_source_info' THEN '출처정보'
       WHEN 'raw_bibliography' THEN '서지'
       WHEN 'raw_event_info' THEN '사건정보'
       ELSE coalesce(a.source_table, '기록') END AS src
WITH a, coalesce(actor, '인물') + ' — ' + src + ' '
        + CASE WHEN coalesce(a.원본id, '') <> '' THEN a.원본id ELSE '#' + toString(a.source_rowid) END
        + ' 언급 (' + toString(coalesce(ord, 1)) + '번째)' AS label
SET a.명칭 = label, a.name = label, a.유형 = '기록 내 인물 언급',
    a.설명 = '해당 기록에서 이 인물이 언급되었다는 사실과 언급 순서만 나타냅니다. 구체적인 행위·일시·장소는 기록 원문을 확인해야 합니다.'
RETURN count(a) AS n
"""


def relabel_mentions(session) -> int:
    """Give Activity nodes labels that say they are mentions of a person in a record."""
    return session.run(MENTION_LABEL_QUERY).single()["n"]


def ensure_index_property(session, index_name: str = "namesIndex", prop: str = "romanized") -> bool:
    """Recreate the full-text index with ``prop`` added if it does not already cover it."""
    rows = list(session.run(
        "SHOW FULLTEXT INDEXES YIELD name, labelsOrTypes, properties WHERE name = $name RETURN labelsOrTypes, properties",
        name=index_name))
    if not rows:
        return False
    labels, props = rows[0]["labelsOrTypes"], rows[0]["properties"]
    if prop in props:
        return False
    props = list(props) + [prop]
    label_expr = "|".join(f"`{label}`" for label in labels)
    prop_expr = ", ".join(f"n.`{p}`" for p in props)
    session.run(f"DROP INDEX `{index_name}` IF EXISTS").consume()
    session.run(f"CREATE FULLTEXT INDEX `{index_name}` IF NOT EXISTS FOR (n:{label_expr}) ON EACH [{prop_expr}]").consume()
    return True


def main() -> int:
    batch_size = 1000
    from pipeline.config import get_neo4j_driver

    driver = get_neo4j_driver(notifications_min_severity="OFF")
    with driver.session() as session:
        rows = session.run(
            "MATCH (p:Person) RETURN elementId(p) AS id, coalesce(p.한글명칭, p.명칭, p.name) AS name").data()
        place_rows = session.run(
            "MATCH (n) WHERE (n:Place OR n:Organization) AND coalesce(n.한글독음, '') = '' "
            "RETURN elementId(n) AS id, coalesce(n.명칭, n.name) AS name, n.한글명칭 AS h_name").data()
        readings = []
        for r in place_rows:
            reading = hanja_reading(r["name"])
            if reading:
                fix_h = not r["h_name"] or _HAN.search(r["h_name"] or "")
                readings.append({"id": r["id"], "reading": reading, "fix_h": bool(fix_h)})
        print(f"Place/Organization nodes without a reading: {len(place_rows)}, readings added: {len(readings)}")
        for row in readings[:5]:
            print("  ", row["reading"])

        updates = [{"id": r["id"], "romanized": v} for r in rows if (v := romanize_name(r["name"]))]
        print(f"Person nodes: {len(rows)}, with romanized variants: {len(updates)}")
        for row in updates[:5]:
            print("  ", row["romanized"][:4], "...")
        print(f"Activity (mention) nodes relabelled: {relabel_mentions(session)}")
        for start in range(0, len(readings), batch_size):
            session.run(
                "UNWIND $rows AS row MATCH (n) WHERE elementId(n) = row.id "
                "SET n.한글독음 = row.reading, "
                "    n.한글명칭 = CASE WHEN row.fix_h THEN row.reading ELSE n.한글명칭 END",
                rows=readings[start:start + batch_size]).consume()
        for start in range(0, len(updates), batch_size):
            session.run(
                "UNWIND $rows AS row MATCH (p) WHERE elementId(p) = row.id SET p.romanized = row.romanized",
                rows=updates[start:start + batch_size]).consume()
        if ensure_index_property(session):
            print("namesIndex recreated with the romanized property.")
    driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
