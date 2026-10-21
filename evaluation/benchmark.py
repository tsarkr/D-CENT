"""Benchmark questions: generation from the event register and validation.

  python -m evaluation dataset             validate dataset/questions.json -> dataset/benchmark.json
  python -m evaluation dataset --generate  regenerate the template questions first (keeps curated and
                                           third-party questions; merges dataset/third_party/*.json)

Validation (applied to template questions; other sources are only checked for duplicates).
A question FAILS when
  - a person name is shorter than 2 characters, a romanised fragment ("Sun"),
    or a title / common noun (NON_NAME_TOKENS)
  - the event name and date in the question disagree with raw_event_info, with
    the date in the event name, or with the period the question states
  - the trap person is a recorded participant of that event (the trap may be true)
  - a trap compares a place with itself
  - it duplicates an earlier question
Wrong particles after a name ("김상열가") are corrected and listed, not failed. The
workbook in dataset/validation/ lists every question and a seeded 30% review sample.

Generated questions (40% multi-hop chaining, 30% spatio-temporal diffusion, 30% false
premise) carry ``source`` = template; hand-written ones curated or third_party.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from pipeline.config import get_pg_connection
from pipeline.text import translate_hanja_name

from evaluation.common import BENCHMARK, QUESTIONS, THIRD_PARTY_DIR, VALIDATION_DIR, add_sheet, new_workbook

# ── Validation ────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Name and token rules (shared)
# ─────────────────────────────────────────────────────────────────────────────
NON_NAME_TOKENS = {'목사', '장로', '전도사', '선교사', '교사', '학생', '면장', '구장', '군수', '순사', '헌병', '의사', '기자', '승려', '농민', '주민'}
DATE_TOKEN_RE = re.compile(r"\d+(?:년|월|일)?")
# Places too common to identify a claim: provinces, recurring cities (not exhaustive),
# and anything with a province/county unit suffix ("전주군", "평양부"; not "임시정부").
GENERIC_PLACES = {
    "경기도", "강원도", "충청북도", "충청남도", "전라북도", "전라남도", "경상북도", "경상남도",
    "황해도", "평안북도", "평안남도", "함경북도", "함경남도",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "황해", "평북", "평남", "함북", "함남",
    "경성", "서울", "한성", "평양", "함흥", "원산", "개성", "인천", "수원", "천안", "공주", "대전", "청주", "전주", "군산",
    "광주", "목포", "대구", "부산", "마산", "진주", "해주", "안악", "의주", "신의주", "선천", "정주", "진남포", "성진",
    "청진", "회령", "춘천", "강릉", "제주", "만주", "간도", "북간도", "연해주", "상하이", "상해", "베이징", "북경",
    "도쿄", "동경", "일본", "조선", "한국", "미국", "중국", "러시아",
}
ADMIN_UNIT_RE = re.compile(r"[가-힣]{1,4}(?:도|군|면|읍)|[가-힣]{2,3}(?<!정)(?<!독)(?<!본)(?<!지)(?<!간)(?<!학)부")
JOSA_PAIRS = [("이", "가"), ("은", "는"), ("을", "를"), ("과", "와")]  # (after a final consonant, after a vowel)


def with_josa(word: str, after_consonant: str = "이", after_vowel: str = "가") -> str:
    """Attach the particle that agrees with the word's final sound: 김상열이 / 김구가."""
    last = word[-1:]
    has_batchim = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + (after_consonant if has_batchim else after_vowel)


def particle(word: str, after_consonant: str, after_vowel: str) -> str:
    """Only the particle that with_josa would attach (for words followed by a parenthesis)."""
    return with_josa(word, after_consonant, after_vowel)[len(word):]


def strip_josa(token: str) -> str:
    """Drop one trailing particle so '이동휘가' can be matched as '이동휘'."""
    for josa in ("에서는", "에서", "으로", "까지", "보다", "에게", "이", "가", "은", "는", "을", "를", "의", "에", "와", "과"):
        if token.endswith(josa) and len(token) - len(josa) >= 2:
            return token[: -len(josa)]
    return token


def is_generic_token(token: str) -> bool:
    """Years, dates, and province/county/city-level place names."""
    return bool(DATE_TOKEN_RE.fullmatch(token)) or token in GENERIC_PLACES or bool(ADMIN_UNIT_RE.fullmatch(token))


def name_problem(name: str) -> str:
    """Reason a string cannot be used as a person name ('' when it is acceptable)."""
    if len(name) < 2:
        return "2글자 미만"
    if re.search(r"[A-Za-z]", name):
        return "로마자 조각"
    if name in NON_NAME_TOKENS:
        return "직책·일반명사"
    if not re.fullmatch(r"[가-힣]{2,4}", name):
        return "인명 형식 아님"
    return ""


def parse_person_list(raw: Any, clean: Callable[[str], str] = lambda s: s) -> list[str]:
    """Person names from a raw_event_info '관련인물' cell.

    Entries are separated by ';'. Roles and romanisations in brackets
    ("김선두(Kim Sun Doo)", "강규찬(… 목사)") are removed before the entry is
    split, so their pieces cannot be mistaken for names.
    """
    persons: list[str] = []
    for entry in re.split(r"[;\n]+", str(raw or "")):
        for token in re.split(r"[,\s]+", re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", entry)):
            name = clean(token)
            if not name_problem(name) and name not in persons:
                persons.append(name)
    return persons


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
NAME_LIST_RE = re.compile(r"([^\s,]+(?:, [^\s,]+)*) 등(?:이|의|에)")   # "A, B, C 등이"
TRAP_SUBJECT_RE = re.compile(r"^(\S+?)(?:가|이)\s")                    # "A가 ..."
INSTRUCTIONS = [
    "벤치마크 문항 자동 검증 결과",
    "",
    "[results] 문항별 판정. PASS=규칙 통과, FAIL=reasons의 사유로 탈락, SKIP=검증 대상 출처가 아님(중복만 확인).",
    "          query 열에는 조사(이/가, 은/는, 을/를, 과/와)를 자동으로 고친 문장이 들어 있고, 고친 내역은 josa_fixes 열에 있습니다.",
    "[human_review] 통과 문항 중 무작위 표본입니다. 질문·정답·함정이 사료와 맞는지 확인하고 verdict에 O 또는 X를 기입해 주십시오.",
    "  - 자동 검증은 형식과 raw_event_info 일치만 확인합니다. 정답 사실(gold_facts)의 역사적 정확성은 사람이 확인해야 합니다.",
]


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def load_event_index() -> dict[str, list[dict[str, Any]]]:
    """title -> [{date, place, persons}] from raw_event_info (read-only)."""
    from pipeline.config import get_pg_connection
    from pipeline.text import translate_hanja_name

    def hangul(text: str) -> str:
        return re.sub(r"[一-鿿]+", lambda m: translate_hanja_name(m.group(0)), text)

    conn = get_pg_connection()
    cur = conn.cursor()
    cur.execute('SELECT "사건명", "시위_시작일자", "시위_행정구역명", "관련인물" FROM raw_event_info WHERE "사건명" IS NOT NULL')
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for title, start, place, persons in cur.fetchall():
        index[_norm(title)].append({
            "date": str(start) if start else "",
            "place": _norm(hangul(str(place or ""))),
            "persons": parse_person_list(persons, lambda token: re.sub(r"[^\w가-힣]", "", hangul(token))),
        })
    cur.close()
    conn.close()
    return dict(index)


def check_item(item: dict[str, Any], events: dict[str, list[dict[str, Any]]] | None) -> tuple[list[str], dict[str, Any], list[str]]:
    """Apply every rule to one question. Returns (fail reasons, question with particles fixed, fixes)."""
    query = item.get("query", "")
    facts = [*item.get("gold_facts", []), *item.get("ground_truth", [])]
    traps = item.get("negative_traps", [])
    reasons: list[str] = []

    # Events named in the question as 'title', each with the date the question gives for it.
    titles = re.findall(r"'([^']+)'", query)
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", query)
    refs: list[tuple[str, str]] = []
    for title in titles:
        attached = re.search(re.escape(f"'{title}'") + r"\((\d{4}-\d{2}-\d{2})\)", query)
        refs.append((title, attached.group(1) if attached else (dates[0] if len(titles) == 1 and len(dates) == 1 else "")))

    # 1. Person names: lists in the gold facts, trap subjects, romanised target entities.
    names = [(name, "gold_facts") for fact in facts for group in NAME_LIST_RE.findall(fact) for name in group.split(", ")]
    names += [(m.group(1), "negative_traps") for m in map(TRAP_SUBJECT_RE.match, traps) if m]
    names += [(str(e).strip(), "target_entities") for e in item.get("target_entities", []) if re.fullmatch(r"[A-Za-z]+", str(e).strip())]
    for name, where in dict.fromkeys(names):
        if name_problem(name):
            reasons.append(f"인명 오류({name_problem(name)}): '{name}' [{where}]")

    # 2. Event name and date.
    period = re.search(r"1919년\s*(\d{1,2})월", query)
    for title, date in refs:
        rows = (events or {}).get(_norm(title), [])
        if events is not None and not rows:
            reasons.append(f"사건명이 raw_event_info에 없음: '{title}'")
            continue
        if rows and date and date not in {r["date"] for r in rows}:
            recorded = ", ".join(sorted({r["date"] or "날짜 없음" for r in rows}))
            reasons.append(f"질문의 날짜({date})가 raw_event_info({recorded})와 다름: '{title}'")
        if not date:
            continue
        month_day = (int(date[5:7]), int(date[8:10]))
        in_title = [(int(m), int(d)) for m, d in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", title)]
        if in_title:
            low, high = min(in_title), max(in_title)
            slack = 1 if "전후" in title else 0
            if not ((low[0], low[1] - slack) <= month_day <= (high[0], high[1] + slack)):
                reasons.append(f"사건명의 날짜({low[0]}월 {low[1]}일)와 질문의 날짜({date})가 다름: '{title}'")
        if period and int(period.group(1)) != month_day[0]:
            reasons.append(f"질문이 밝힌 시기(1919년 {period.group(1)}월)와 사건 날짜({date})가 다름: '{title}'")

    # 3. Traps: self-contradiction, or the trap person is a recorded participant of the event it names.
    for trap in traps:
        comparison = re.match(r"^(.+?)(?:가|이)\s+(.+?)보다", trap)
        if comparison:
            left, right = (re.sub(r"\W+", "", re.sub(r"\s*(?:시위|사건)$", "", side)) for side in comparison.groups())
            if left and left == right:
                reasons.append(f"함정 자기모순(비교 양쪽이 같은 지명): '{trap}'")
                continue
        subject = TRAP_SUBJECT_RE.match(trap)
        if not subject:
            continue
        # The event the trap is about: named by title or date, else by place, else the question's only event.
        targets = [(t, d) for t, d in refs if t in trap or (d and d in trap)]
        if not targets:
            targets = [(t, d) for t, d in refs
                       if any(row["place"] and row["place"] in _norm(trap) for row in (events or {}).get(_norm(t), []))]
        if not targets and len(refs) == 1:
            targets = refs
        for title, date in targets:
            participants = {p for row in (events or {}).get(_norm(title), []) for p in row["persons"]}
            for fact in facts:
                if title in fact or (date and date in fact):
                    participants.update(name for group in NAME_LIST_RE.findall(fact) for name in group.split(", "))
            if subject.group(1) in participants:
                reasons.append(f"함정이 사실일 수 있음('{subject.group(1)}'이(가) '{title}' 참여자 목록에 있음): '{trap}'")
                break

    # 4. Particles that disagree with the final sound of a name or event title (fixed, not failed).
    words = {name for name, _ in names} | set(titles)
    words |= {str(e) for e in item.get("target_entities", []) if not name_problem(str(e)) and not is_generic_token(str(e))}
    fixed = json.loads(json.dumps(item, ensure_ascii=False))
    fixes: list[str] = []

    def repair(text: str) -> str:
        for word in sorted((w for w in words if "가" <= w[-1:] <= "힣"), key=len, reverse=True):
            for pair in JOSA_PAIRS:
                right = with_josa(word, *pair)
                wrong = word + (pair[1] if right.endswith(pair[0]) else pair[0])
                text, n = re.subn(r"(?<![가-힣])" + re.escape(wrong) + r"(?=[\s,.?]|$)", right, text)
                if n and f"{wrong} → {right}" not in fixes:
                    fixes.append(f"{wrong} → {right}")
        return text

    for field in ("query", "gold_facts", "ground_truth", "negative_traps"):
        if isinstance(fixed.get(field), str):
            fixed[field] = repair(fixed[field])
        elif isinstance(fixed.get(field), list):
            fixed[field] = [repair(v) for v in fixed[field]]
    return reasons, fixed, fixes


def validate_dataset(dataset_path: Path, out_dir: Path = VALIDATION_DIR, seed: int = 42, save_passed: Path | None = None) -> dict[str, Any]:
    """Validate a benchmark file, write the workbook and summary, print the result."""
    items = json.loads(dataset_path.read_text(encoding="utf-8"))
    try:
        events = load_event_index()
    except Exception as exc:  # the rules that do not need the database still run
        events = None
        print(f"⚠️ raw_event_info를 읽지 못해 DB 대조(사건명·날짜·참여자)를 건너뜁니다: {str(exc)[:200]}")

    rows, kept = [], []
    seen: dict[str, str] = {}
    for item in items:
        source = item.get("source") or "unknown"
        query = item.get("query", "")
        titles = sorted({_norm(t) for t in re.findall(r"'([^']+)'", query)})
        # Same wording, or same category + same events + same leading subject = the same question again.
        keys = [re.sub(r"\W+", "", query)]
        if titles:
            keys.append(str((item.get("category", ""), titles, query.split("'")[0].split()[:1])))
        duplicate_of = next((seen[k] for k in keys if k in seen), "")
        for k in keys:
            seen.setdefault(k, item.get("id", ""))

        reasons = [f"질문 중복: {duplicate_of}와 같은 질문·사건 조합"] if duplicate_of else []
        checked = source in ("template", "unknown")
        fixed, fixes = item, []
        if checked:
            found, fixed, fixes = check_item(item, events)
            reasons += found
        status = "FAIL" if reasons else ("PASS" if checked else "SKIP")
        if status != "FAIL":
            kept.append(fixed)
        rows.append({"id": item.get("id", ""), "source": source, "category": item.get("category", ""), "status": status,
                     "reasons": "\n".join(reasons), "josa_fixes": "\n".join(fixes), "query": fixed.get("query", ""),
                     "gold_facts": "\n".join(fixed.get("gold_facts", [])), "negative_traps": "\n".join(fixed.get("negative_traps", []))})

    passed = [r for r in rows if r["status"] == "PASS"]
    review = random.Random(seed).sample(passed, math.ceil(len(passed) * 0.3))

    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"{dataset_path.stem}_validation.xlsx"
    wb = new_workbook(INSTRUCTIONS)
    add_sheet(wb, "results", ["id", "source", "category", "status", "reasons", "josa_fixes", "query"], rows,
              widths={"id": 12, "category": 26, "status": 8, "reasons": 80, "josa_fixes": 40, "query": 80})
    add_sheet(wb, "human_review", ["id", "source", "category", "query", "gold_facts", "negative_traps", "verdict", "note"], review,
              widths={"id": 12, "category": 26, "query": 70, "gold_facts": 80, "negative_traps": 70, "note": 30},
              choices={"verdict": ["O", "X"]})
    wb.save(xlsx_path)

    by_source: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_source[r["source"]][r["status"]] += 1
    summary = {
        "dataset": dataset_path.name,
        "n_items": len(items),
        "status": dict(Counter(r["status"] for r in rows)),
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "fail_reasons": dict(Counter(re.split(r"[:(]", line)[0] for r in rows for line in r["reasons"].split("\n") if line).most_common()),
        "failed": {r["id"]: r["reasons"].split("\n") for r in rows if r["status"] == "FAIL"},
        "josa_fixes": sum(len(r["josa_fixes"].split("\n")) for r in rows if r["josa_fixes"]),
        "db_checks": events is not None,
        "review_sample": {"seed": seed, "fraction": 0.3, "ids": sorted(r["id"] for r in review)},
    }
    summary_path = out_dir / f"{dataset_path.stem}_validation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if save_passed:
        save_passed.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"🔎 {dataset_path.name}: {len(items)}문항 — " + ", ".join(f"{k} {v}" for k, v in summary["status"].items()))
    for reason, n in summary["fail_reasons"].items():
        print(f"   ✗ {reason}: {n}건")
    print(f"   조사 자동 수정 {summary['josa_fixes']}건, 사람 검토 표본 {len(review)}문항 (seed={seed})")
    print(f"💾 {xlsx_path}\n💾 {summary_path}" + (f"\n💾 {save_passed} ({len(kept)}문항)" if save_passed else ""))
    return summary


# ── Generation from the event register ───────────────────────────────────────


def clean_name(raw: str) -> str:
    """Clean person or place name, translating Hanja if present."""
    c = re.sub(r'\(.*?\)', '', raw)
    c = re.sub(r'\[.*?\]', '', c)
    c = re.sub(r'[\u4e00-\u9fff]+', lambda m: translate_hanja_name(m.group(0)), c)
    c = re.sub(r'[^\w가-힣]', '', c).strip()
    return c


def load_events_from_pg() -> list[dict[str, Any]]:
    conn = get_pg_connection()
    cur = conn.cursor()
    query = """
        SELECT "아이디", "사건명", "시위_시작일자", "시위_종료일자", "시위_행정구역명", "관련인물", "관련시위_아이디"
        FROM raw_event_info
        WHERE "사건명" IS NOT NULL AND "시위_행정구역명" IS NOT NULL
        ORDER BY "시위_시작일자" ASC NULLS LAST, "아이디" ASC
    """
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    events = []
    for r in rows:
        ev_id, title, s_date, e_date, place, persons_raw, rel_ids = r
        # Hangul names only: drops romanised fragments ("Sun") and titles ("목사").
        persons = parse_person_list(persons_raw, clean_name) if persons_raw else []

        clean_place = re.sub(r'[\u4e00-\u9fff]+', lambda m: translate_hanja_name(m.group(0)), str(place))
        clean_place = re.sub(r'\s+', ' ', clean_place).strip()

        events.append({
            "id": str(ev_id),
            "title": str(title).strip(),
            "start_date": str(s_date) if s_date else "",
            "end_date": str(e_date) if e_date else "",
            "place": clean_place,
            "persons": persons,
            "related_event_ids": [x.strip() for x in str(rel_ids).split(';') if x.strip()] if rel_ids else []
        })
    return events


REQUIRED_FIELDS = ("id", "category", "query", "target_entities", "gold_facts", "negative_traps")
LEGACY_CURATED_COUNT = 13  # datasets written before the `source` field: first 13 items are hand-written


def load_third_party(path: Path) -> list[dict[str, Any]]:
    """Load and validate questions written by an external author."""
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"{path}: 최상위 구조는 문항 리스트여야 합니다.")
    for n, item in enumerate(items, 1):
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise ValueError(f"{path} 문항 {n}: 필수 필드 누락 {missing}")
        author = str(item.get("author", "")).strip()
        if not author or author == "REPLACE_ME":
            raise ValueError(f"{path} 문항 {n} ({item['id']}): 외부 출제자를 'author'에 기입해야 합니다.")
        if not item["gold_facts"]:
            raise ValueError(f"{path} 문항 {n} ({item['id']}): gold_facts가 비어 있습니다.")
        item["source"] = "third_party"
        item.setdefault("hops", 0)
    return items


def generate_benchmark_queries(target_count: int = 120, third_party_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    events = load_events_from_pg()
    ev_by_id = {e["id"]: e for e in events}
    # Every generated question is checked with the validation rules before it is kept,
    # so that `dataset --generate` yields questions that pass validation.
    event_index = load_event_index()
    seen_pairs: set[tuple[str, ...]] = set()
    rejected: Counter = Counter()

    def accept(item: dict[str, Any], pair: tuple[str, ...]) -> bool:
        if pair in seen_pairs:
            rejected["같은 사건 조합"] += 1
            return False
        reasons, _, _ = check_item(item, event_index)
        if reasons:
            rejected[re.split(r"[:(]", reasons[0])[0]] += 1
            return False
        seen_pairs.add(pair)
        return True

    # 1. Keep hand-written questions (authors' and third-party); template items are regenerated.
    existing = []
    if QUESTIONS.exists():
        try:
            existing = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    curated = []
    for idx, item in enumerate(existing):
        source = item.get("source") or ("curated" if idx < LEGACY_CURATED_COUNT else "template")
        if source != "template":
            item["source"] = source
            curated.append(item)
    for path in third_party_paths or []:
        curated.extend(load_third_party(path))

    generated = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    for item in curated:
        if item["id"] in seen_ids or item["query"] in seen_queries:
            continue  # an already-merged third-party file was passed again
        seen_ids.add(item["id"])
        seen_queries.add(item["query"])
        generated.append(item)
    curated = list(generated)

    # Count how many of each category we need
    needed_total = target_count - len(generated)
    target_mh = int(needed_total * 0.40)
    target_st = int(needed_total * 0.30)
    target_zc = needed_total - target_mh - target_st

    print(f"📊 [데이터셋 생성] 기존 엄선 질의: {len(curated)}건, 추가 생성 목표: {needed_total}건 (MH:{target_mh}, ST:{target_st}, ZC:{target_zc})")

    # -------------------------------------------------------------------------
    # Category 1: Multi-hop Chaining (연계 사건 간 인과 및 주동자 추론)
    # -------------------------------------------------------------------------
    mh_count = 0
    for ev in events:
        if mh_count >= target_mh:
            break
        if not ev["related_event_ids"] or not ev["persons"]:
            continue

        for r_id in ev["related_event_ids"]:
            target_ev = ev_by_id.get(r_id)
            if not target_ev or not target_ev["persons"] or target_ev["id"] == ev["id"]:
                continue
            # Trap subjects must not be recorded participants of the event the trap places them in.
            outsider_a = next((p for p in ev["persons"] if p not in target_ev["persons"]), None)
            outsider_b = next((p for p in target_ev["persons"] if p not in ev["persons"]), None)
            if not outsider_a or not outsider_b or ev["title"] == target_ev["title"]:
                continue

            # Multi-hop between ev and target_ev
            p1_str = ", ".join(ev["persons"][:3])
            p2_str = ", ".join(target_ev["persons"][:3])
            q_id = f"Q_MH_{len(generated) + 1:02d}"
            query_text = (
                f"'{ev['title']}'(발생지: {ev['place']}){particle(ev['title'], '과', '와')} 이와 관련된 사건으로 기록된 '{target_ev['title']}'(발생지: {target_ev['place']})에 "
                f"각각 관련 인물로 기록된 사람은 누구이며, 두 사건은 기록에서 어떻게 연결되어 있는가?"
            )
            if query_text in seen_queries:
                continue

            item = {
                "id": q_id,
                "category": "Multi-hop Chaining",
                "source": "template",
                "query": query_text,
                "hops": 2,
                "target_entities": list(set([ev["title"][:15], target_ev["title"][:15], ev["place"].split()[-1], target_ev["place"].split()[-1]] + ev["persons"][:2] + target_ev["persons"][:2])),
                # Reference facts state only what the event register records: related persons and the link.
                "gold_facts": [
                    f"{ev['title']}의 관련 인물로 {p1_str} 등이 기록되어 있음",
                    f"{target_ev['title']}의 관련 인물로 {p2_str} 등이 기록되어 있음",
                    f"사건 대장은 {with_josa(ev['title'], '과', '와')} {with_josa(target_ev['title'], '을', '를')} 관련 사건으로 연결함",
                ],
                "negative_traps": [
                    f"{with_josa(outsider_a)} {target_ev['place']}의 {target_ev['title']} 현장을 직접 지휘했다",
                    f"{with_josa(outsider_b)} {ev['title']}의 총책임자로 체포되었다"
                ]
            }
            if not accept(item, tuple(sorted((ev["title"], target_ev["title"])))):
                continue
            generated.append(item)
            seen_queries.add(query_text)
            mh_count += 1
            break

    # -------------------------------------------------------------------------
    # Category 2: Spatial-Temporal Diffusion (시공간 확산 질의)
    # -------------------------------------------------------------------------
    st_count = 0
    # Group events by broad region / province
    regions = {}
    for ev in events:
        if not ev["persons"] or not ev["start_date"]:
            continue
        prov = ev["place"].split()[0] if ev["place"] else "기타"
        if prov not in regions:
            regions[prov] = []
        regions[prov].append(ev)

    # Walk provinces round-robin, pairing the k-th earliest with the k-th latest
    # event, so the category can grow beyond one question per province.
    max_pairs = max((len(v) // 2 for v in regions.values()), default=0)
    st_pairs = [
        (prov, p_events[k], p_events[-1 - k])
        for k in range(max_pairs)
        for prov, p_events in regions.items()
        if len(p_events) // 2 > k
    ]
    for prov, e1, e2 in st_pairs:
        if st_count >= target_st:
            break
        if e1["start_date"] == e2["start_date"] or not e1["persons"] or not e2["persons"] or e1["place"] == e2["place"]:
            continue
        mover = next((p for p in e1["persons"] if p not in e2["persons"]), None)
        if not mover:
            continue
        m1, m2 = int(e1["start_date"][5:7]), int(e2["start_date"][5:7])
        period = f"1919년 {m1}월" if m1 == m2 else "1919년"

        q_id = f"Q_ST_{len(generated) + 1:02d}"
        query_text = (
            f"{period} {prov} 지역의 '{e1['title']}'({e1['start_date']}){particle(e1['title'], '과', '와')} '{e2['title']}'({e2['start_date']}){particle(e2['title'], '은', '는')} "
            f"어떤 순서로 어디에서 일어났으며, 각 시위에 관련 인물로 기록된 사람은 누구인가?"
        )
        if query_text in seen_queries:
            continue

        p1 = ", ".join(e1["persons"][:2])
        p2 = ", ".join(e2["persons"][:2])
        item = {
            "id": q_id,
            "category": "Spatial-Temporal Diffusion",
            "source": "template",
            "query": query_text,
            "hops": 2,
            "target_entities": list(set([prov, e1["place"].split()[-1], e2["place"].split()[-1]] + e1["persons"][:2] + e2["persons"][:2])),
            # Reference facts: dates, places, and recorded persons only; the order follows from the dates.
            "gold_facts": [
                f"{with_josa(e1['title'], '은', '는')} {e1['start_date']} {e1['place']}에서 일어났으며 관련 인물로 {p1} 등이 기록되어 있음",
                f"{with_josa(e2['title'], '은', '는')} {e2['start_date']} {e2['place']}에서 일어났으며 관련 인물로 {p2} 등이 기록되어 있음",
                f"{with_josa(e1['title'], '이', '가')} {e2['title']}보다 먼저 일어남",
            ],
            "negative_traps": [
                f"{with_josa(mover)} {e2['start_date']} {e2['place']} 시위까지 직접 이동하여 주동했다",
                f"{e2['place']} 시위가 {e1['place']}보다 먼저 발생한 발원지이다"
            ]
        }
        if not accept(item, (e1["title"], e2["title"])):
            continue
        generated.append(item)
        seen_queries.add(query_text)
        st_count += 1

    # -------------------------------------------------------------------------
    # Category 3: Zero-Correlation Trap (교차 귀속 환각 유도 질의)
    # -------------------------------------------------------------------------
    zc_count = 0
    famous_figures = [
        ("김구", "황해도", "상하이 임시정부"),
        ("안창호", "미국 체류", "임시정부 내무총장"),
        ("유관순", "충남 천안 아우내", "공주 영명학교 및 이화학당"),
        ("한용운", "경성 태화관", "백담사 및 만해사상"),
        ("이동휘", "러시아 연해주", "한인사회당"),
        ("신채호", "베이징", "신대한 동맹"),
        ("조만식", "평양 기독교계", "조선물산장려회"),
        ("여운형", "상하이 신한청년당", "파리강화회의 대표 파견")
    ]

    for ev in events:
        if zc_count >= target_zc:
            break
        if not ev["persons"] or not ev["place"]:
            continue
        real_leaders = ", ".join(ev["persons"][:3])
        # Pair the event with a well-known figure who is not recorded for it (try each in turn).
        for offset in range(len(famous_figures)):
            fig_name, fig_loc, _ = famous_figures[(zc_count + offset) % len(famous_figures)]
            if fig_name in ev["persons"] or fig_loc in ev["place"]:
                continue
            q_id = f"Q_ZC_{len(generated) + 1:02d}"
            query_text = (
                f"{with_josa(fig_name)} {ev['start_date'] if ev['start_date'] else '1919년 3월'} {ev['place']}에서 일어난 "
                f"'{ev['title']}' 현장에서 군중을 직접 인솔하며 만세 시위를 지휘했는가?"
            )
            if query_text in seen_queries:
                continue
            item = {
                "id": q_id,
                "category": "Zero-Correlation Trap",
                "source": "template",
                "query": query_text,
                "hops": 0,
                "target_entities": [fig_name, ev["place"].split()[-1], ev["title"][:12]] + ev["persons"][:2],
                # Reference facts: what the register records, and that it does not record the figure.
                "gold_facts": [
                    f"{ev['title']}의 관련 인물로 {real_leaders} 등이 기록되어 있음",
                    f"{ev['title']}의 관련 인물 기록에 {fig_name}의 이름은 없음",
                    f"{with_josa(fig_name)} 이 시위 현장을 지휘했다는 기록은 확인되지 않음",
                ],
                "negative_traps": [
                    f"{with_josa(fig_name)} {ev['place']} 시위대 선두에서 태극기를 흔들다 현장에서 일제 순사에게 연행되었다",
                    f"{with_josa(fig_name)} {ev['title']}의 격문을 직접 작성하여 배포하였다"
                ]
            }
            if not accept(item, (fig_name, ev["title"])):
                continue
            generated.append(item)
            seen_queries.add(query_text)
            zc_count += 1
            break

    print(f"✅ 총 {len(generated)}건의 정량 평가 벤치마크 데이터셋 구축 완료!")
    if rejected:
        print("   검증 규칙에 걸려 버린 후보: " + ", ".join(f"{k} {v}" for k, v in rejected.most_common()))
    duplicate_ids = [i for i, n in Counter(item["id"] for item in generated).items() if n > 1]
    if duplicate_ids:
        raise ValueError(f"문항 id가 중복됩니다: {duplicate_ids}")
    by_source = Counter(item["source"] for item in generated)
    by_category = Counter(item["category"] for item in generated)
    print(f"   출처별: {dict(by_source)}")
    print(f"   범주별: {dict(by_category)}")
    if len(generated) < target_count:
        print(f"⚠️ 목표 {target_count}건에 미달했습니다 (템플릿을 채울 사건 조합 부족).")
    if not by_source.get("third_party"):
        print("⚠️ 제3자 출제 문항이 없습니다. --third-party 로 외부 출제 파일을 병합하세요.")
    return generated


def build(generate: bool = False) -> int:
    """Optionally regenerate dataset/questions.json, then validate it into dataset/benchmark.json."""
    if generate:
        third_party = sorted(p for p in THIRD_PARTY_DIR.glob("*.json")) if THIRD_PARTY_DIR.exists() else []
        questions = generate_benchmark_queries(target_count=120, third_party_paths=third_party)
        QUESTIONS.write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 {QUESTIONS} ({len(questions)}문항)")
    if not QUESTIONS.exists():
        raise SystemExit(f"{QUESTIONS}가 없습니다. `python -m evaluation dataset --generate`로 만드세요.")
    validate_dataset(QUESTIONS, VALIDATION_DIR, seed=42, save_passed=BENCHMARK)
    write_composition_table(json.loads(BENCHMARK.read_text(encoding="utf-8")), VALIDATION_DIR / "benchmark_table.tex")
    return 0


CATEGORY_NAMES = {
    "Spatial-Temporal Diffusion": "Spatio-temporal diffusion",
    "Multi-hop Chaining": "Multi-hop chaining",
    "Zero-Correlation Trap": "False premise (zero-correlation trap)",
    "Kinship & Affiliation": "Kinship and affiliation",
}


def write_composition_table(items: list[dict[str, Any]], path: Path) -> None:
    """Benchmark composition by category and source, printed and written as a LaTeX table."""
    counts = Counter((item["category"], item.get("source") or "template") for item in items)
    sources = [s for s in ("curated", "third_party", "template") if any(k[1] == s for k in counts)]
    categories = sorted({c for c, _ in counts}, key=lambda c: -sum(n for (cc, _), n in counts.items() if cc == c))
    print("\n벤치마크 구성 (범주 × 출처)")
    lines = [r"\begin{table}[t]", r"\centering", r"\caption{Benchmark composition.}", r"\label{tab:benchmark}",
             r"\begin{tabular}{l" + "r" * (len(sources) + 1) + "}", r"\toprule",
             r"\textbf{Category} & " + " & ".join(rf"\textbf{{{s.replace('_', ' ').capitalize()}}}" for s in sources) + r" & \textbf{Total} \\",
             r"\midrule"]
    for c in categories:
        row = [counts.get((c, s), 0) for s in sources]
        print(f"  {c:<30} " + "  ".join(f"{s} {n}" for s, n in zip(sources, row)) + f"  total {sum(row)}")
        lines.append(f"{CATEGORY_NAMES.get(c, c)} & " + " & ".join(map(str, row)) + f" & {sum(row)} \\\\")
    totals = [sum(n for (_, s), n in counts.items() if s == src) for src in sources]
    lines += [r"\midrule", "Total & " + " & ".join(map(str, totals)) + f" & {sum(totals)} \\\\", r"\bottomrule",
              r"\end{tabular}", r"\end{table}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 {path}")
