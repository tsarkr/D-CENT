"""Automatic answer metrics, citation verification, and paired comparison of systems.

Per answer (scores.csv): reference-fact recall, trap endorsement, decline, entity hit rate and
MRR of the evidence, and citation counts. Per run: means with 95% bootstrap intervals by
category and by question source, and paired sign-flip permutation tests with Holm correction.

Citations. An answer may cite three kinds of things:

  record id       raw_bib_123 / raw_bibliography-123 / raw_event_info-45 (table and row of a
                  record table). Valid only if that record is part of the evidence passed to
                  the generator.
  internal label  relation types (P70_documents, P106i...), block tags (GRAPH_TOPOLOGY, g_3,
                  "블록 3"), "Graph DB", ... Never valid: a reader cannot follow them to a source.
  named source    a newspaper issue, event name, or document title written out. Valid if the
                  same string occurs in the evidence.

``clean_answer`` removes invalid record ids and internal labels and leaves the sentence intact.
"""

from __future__ import annotations

import csv
import itertools
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation.benchmark import ADMIN_UNIT_RE, DATE_TOKEN_RE, is_generic_token, strip_josa
from evaluation.common import bootstrap_ci, holm_adjust, paired_permutation_test

# ── Answer scores ────────────────────────────────────────────────────────────
DECLINE_MARKERS = ("자료에서 확인되지 않", "확인할 수 없", "확인되지 않아", "찾을 수 없었습니다", "서술하기 어렵", "cannot be confirmed")


def is_generation_failure(answer: str) -> bool:
    """True when the pipeline produced no answer (API error), as opposed to a wrong one."""
    stripped = answer.strip()
    return not stripped or bool(re.match(r"\[[A-Za-z ]*Error", stripped)) or stripped.startswith((
        "Gemini 오류", "AI 요청 실패", "AI 응답을 생성하지 못했습니다.",
        "API Key가 설정되지 않았습니다.", "API key is not configured.",
    ))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text) if s.strip()]


def _is_negative(text: str) -> bool:
    # Connectives and idioms that read as affirmative ("뿐만 아니라", "굴하지 않고", "틀림없다") are not denials.
    text = re.sub(r"뿐(?:만)?\s*아니라|지\s*(?:않|못하)고|(?:틀림|어김|빠짐|끊임|거침|아낌)\s*없", " ", text)
    return bool(re.search(
        r"지\s*않|지\s*못|아니[다었며라고]|아닌|아닙니|아님|없[다습는었음으이고]|확인되지|확인할\s*수|찾을\s*수|"
        r"찾아볼\s*수|단정할\s*수|어렵|불가능|불분명|부재|무관(?!학교)|허위|허구|사실무근|모순|상반|배치(?:되|됩)|범위를\s*벗어|"
        r"사실과\s*다[르릅]|근거[가는]?\s*없|\b(?:not|never|cannot|unconfirmed|false)\b", text, re.I))


# Version of the automatic answer scores; `report --rescore` rewrites scores.csv with the current one.
METRICS_VERSION = 2

# Words of the reference facts and traps that state the kind of claim rather than its content
# ("관련 인물로 … 기록되어 있음"). They occur in almost every answer and are never matched.
FACT_FUNCTION_WORDS = {
    "에서", "으로", "하고", "하여", "등이", "등", "또는", "이후", "당시", "직후", "직접",
    "기록", "기록되어", "기록된", "기록에", "기록은", "있음", "있다", "없음", "없다", "않음", "확인되지",
    "관련", "인물", "인물로", "이름은", "사건", "사건의", "참여자로", "대장은", "연결함", "먼저", "일어남",
    "일어났으며", "검색", "검색된", "근거의", "근거에는", "근거만으로는", "그래프에는", "전개함", "받음", "형성됨",
}
_VERB_END_RE = re.compile(
    r"(?:하였다|하였으며|하였고|했다는|했다|했으며|했고|하여|하며|하고|되었다|되었으며|되었고|되어|된|"
    r"이다|였다|었다|았다|한다|하다|함|됨|으며)$")


def _stem(token: str) -> str:
    """'김상열이' -> '김상열', '지휘했다는' -> '지휘', '체포되었다' -> '체포'."""
    stem = strip_josa(token)
    cut = _VERB_END_RE.sub("", stem)
    return cut if len(cut) >= 2 else stem


def _clauses(sentence: str) -> list[str]:
    """Clauses of a sentence, so that a denial in one clause does not cancel a list in another."""
    return [c for c in re.split(r"[,;，、]\s*", sentence) if c.strip()]


def fact_keywords(fact: str, question: str = "") -> list[str]:
    """Content words of a reference fact that the question does not already contain.

    A fact is scored only on what an answer must add to the question. Without this, an answer that
    restates the question's incident names covers most generated facts (they repeat those names).
    """
    words = []
    for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", fact):
        stem = _stem(token)
        if token in FACT_FUNCTION_WORDS or stem in FACT_FUNCTION_WORDS or DATE_TOKEN_RE.fullmatch(stem):
            continue
        if question and stem in question:
            continue
        words.append(stem)
    return list(dict.fromkeys(words))


def evaluate_faithfulness(answer: str, gold_facts: list[str], question: str = "") -> tuple[float | None, int, int]:
    """Recall of the reference facts: (percent of scorable facts covered, covered, scorable).

    A fact is covered when one answer sentence contains at least half of its content words
    (``fact_keywords``) in clauses of the same polarity as the fact. Facts whose content is entirely
    in the question (the order of two named incidents, that a figure is not recorded) cannot be
    scored lexically and are left to the raters; with no scorable fact the score is None.
    """
    covered = scorable = 0
    for fact in gold_facts:
        keywords = fact_keywords(fact, question)
        if not keywords:
            continue
        scorable += 1
        need = max(1, (len(keywords) + 1) // 2)
        negative_fact = _is_negative(fact)
        for sentence in _sentences(answer):
            if negative_fact:
                hits = sum(kw in sentence for kw in keywords) if _is_negative(sentence) else 0
            else:
                text = " ".join(c for c in _clauses(sentence) if not _is_negative(c))
                hits = sum(kw in text for kw in keywords)
            if hits >= need:
                covered += 1
                break
    if not scorable:
        return None, 0, 0
    return round(covered / scorable * 100.0, 2), covered, scorable


def evidence_items(contexts: list[str]) -> list[str]:
    """Use record envelopes or individual path/triple lines for every system."""
    items = []
    for context in contexts:
        for part in re.split(r'(<SOURCE_EVIDENCE\b[\s\S]*?(?:</SOURCE_EVIDENCE>|(?=<SOURCE_EVIDENCE\b)|\Z))', context):
            if part.startswith('<SOURCE_EVIDENCE'):
                items.append(part)
            else:
                headers = {
                    '<GRAPH_TOPOLOGY>', '</GRAPH_TOPOLOGY>',
                    '[지식그래프 다중 홉 탐색 결과]', '[Knowledge Graph Multi-hop Results]',
                    '[知識グラフ多重ホップ探索結果]', '[知识图谱多跳探索结果]',
                    '[1차 사료 원문 (PostgreSQL)]', '[Primary Archival Sources (PostgreSQL)]',
                    '[一次史料原文（PostgreSQL所蔵公文書）]', '[一手史料原文（PostgreSQL历史档案）]',
                }
                items.extend(line.strip() for line in part.splitlines() if line.strip() and line.strip() not in headers)
    return items


def average_precision(relevant: list[int]) -> float:
    hits, weighted = 0, 0.0
    for rank, value in enumerate(relevant, 1):
        hits += value
        if value:
            weighted += hits / rank
    return weighted / hits if hits else 0.0


def evaluate_retrieval_direct(query_item: dict[str, Any], contexts: list[str]) -> dict[str, float]:
    """Compute entity Hit Rate@K and MRR without an LLM judge."""
    targets = [str(value).strip().lower() for value in query_item.get("target_entities", []) if str(value).strip()]
    normalized = [context.lower() for context in contexts]
    if not targets:
        return {"entity_hit_rate_at_k": 0.0, "entity_mrr": 0.0}
    hits = sum(any(target in context for context in normalized) for target in targets)
    first_rank = next(
        (rank for rank, context in enumerate(normalized, 1)
         if any(target in context for target in targets)),
        0,
    )
    return {
        "entity_hit_rate_at_k": round(hits / len(targets), 4),
        "entity_mrr": round(1 / first_rank, 4) if first_rank else 0.0,
    }


def citation_stats(answer: str, contexts: list[str]) -> dict[str, int]:
    """Count record-id citations (valid / total), internal-label citations, and named sources found in the evidence."""
    cites = verify_answer(answer, contexts)
    return {
        "cite_ids_valid": sum(1 for c in cites if c["kind"] == "record_id" and c["valid"]),
        "cite_ids_total": sum(1 for c in cites if c["kind"] == "record_id"),
        "cite_internal_labels": sum(1 for c in cites if c["kind"] == "internal_label"),
        "cite_named_found": sum(1 for c in cites if c["kind"] == "named_source" and c["valid"]),
        "cite_named_total": sum(1 for c in cites if c["kind"] == "named_source"),
    }


# Predicates too general to identify what a trap asserts.
GENERIC_PREDICATES = {"진행", "발생", "전개", "참여", "활동", "기록", "관련", "시위", "만세", "사건"}
# A sentence that restates or asks the question does not assert it ("…지휘했는지 질의하였다").
NON_ASSERTIVE_RE = re.compile(r"는지|인지|여부|질의|질문|묻는|물었")
QUESTION_VERB_RE = re.compile(r"([가-힣]{2,})(?:하고|하며|하여|했는가|하였는가|했는지|하였는지|했나|하였나)(?![가-힣])")


def _assertions(answer: str) -> list[str]:
    """Answer sentences that can assert a claim: headings and restated questions are left out."""
    out = []
    for line in re.split(r"\n+", answer or ""):
        line = line.strip()
        if not line or line.startswith("#") or (line.startswith("**") and line.endswith("**")):
            continue
        for s in re.split(r"(?<=[.!?。])\s+", line):
            s = s.strip().lstrip("*-• ").strip()
            ends = s.rstrip("*.)! ").endswith(("다", "음", "함", "됨", "요")) or s.endswith((".", "!", "。"))
            if s and (ends or len(s) >= 60) and not NON_ASSERTIVE_RE.search(s):
                out.append(s)
    return out


# "X가 …지휘했다는 기록은 확인되지 않는다": the denial covers the whole embedded claim.
EMBEDDED_DENIAL_RE = re.compile(r"(?:다는|다고|다거나|했는가|하였는가|는가에)\s*(?:\S+\s*){0,2}?(?:기록|주장|증거|내용|전제|가설|사실|점|정황|근거|흔적|대한)")


def _endorsed(sentences: list[str], subject: str, predicates: list[str], context: list[str]) -> list[str]:
    """Sentences in which one non-negated clause has the subject, a predicate, and (if given) a context term."""
    hits = []
    for s in sentences:
        if _is_negative(s) and EMBEDDED_DENIAL_RE.search(s):
            continue
        if any(subject in c and any(p in c for p in predicates) and not _is_negative(c)
               and (not context or any(x in re.sub(r"\s+", "", c) for x in context)) for c in _clauses(s)):
            hits.append(s)
    return hits


def evaluate_hallucination(
    answer: str, negative_traps: list[str], category: str, entities: list[str] | None = None, question: str = "",
) -> tuple[float | None, int, str]:
    """Trap endorsement: (percent of judged traps asserted, traps asserted, flagged sentences).

    A trap is asserted when an answer sentence states, in a clause without negation, the trap's
    subject (its leading name, or a question entity) together with what the trap claims (the stems of
    its last two content words that the question does not contain: "지휘", "연행", "작성"). Merely
    naming the subject next to a place, as an answer listing the recorded participants does, is not
    an assertion. For false-premise questions the premise of the question is judged as one more trap:
    the figure, the action asked about ("지휘", "낭독"), and the incident or place of the question.
    Headings and sentences that restate the question are skipped. Traps without a recognizable
    subject or claim are not judged; with none judged the score is None.
    """
    hints = [str(e).strip() for e in entities or [] if str(e).strip() and not is_generic_token(str(e).strip())]
    sentences = _assertions(answer)
    judged = triggered = 0
    flagged: list[str] = []

    def record(hits: list[str]) -> None:
        nonlocal triggered
        if hits:
            triggered += 1
            flagged.extend(s for s in hits if s not in flagged)

    for trap in negative_traps or []:
        tokens = [_stem(k) for k in re.findall(r"[가-힣A-Za-z0-9]{2,}", trap)]
        tokens = list(dict.fromkeys(t for t in tokens if t not in FACT_FUNCTION_WORDS and not DATE_TOKEN_RE.fullmatch(t)))
        anchors = [t for i, t in enumerate(tokens)
                   if not is_generic_token(t) and (i == 0 or any(h in t or h.startswith(t) for h in hints))]
        if not anchors:
            continue  # no name or proper noun: cannot be matched reliably
        content = [t for t in tokens if t not in anchors and t not in GENERIC_PREDICATES and not is_generic_token(t)]
        predicates = [t for t in content[-2:] if not (question and t in question)]
        if not predicates:
            continue  # what the trap claims is already in the question
        # the trap's county- or township-level place, if it names one, must be in the sentence as well
        places = [t for t in dict.fromkeys(_stem(k) for k in re.findall(r"[가-힣]{2,}", trap))
                  if ADMIN_UNIT_RE.fullmatch(t) and not t.endswith("도")]
        judged += 1
        record(_endorsed(sentences, anchors[0], predicates, places))

    if category == "Zero-Correlation Trap" and question and entities:
        people = [str(e).strip() for e in entities if re.fullmatch(r"[가-힣]{2,4}", str(e).strip()) and not is_generic_token(str(e).strip())]
        verbs = [v for v in dict.fromkeys(QUESTION_VERB_RE.findall(question)) if v not in GENERIC_PREDICATES]
        if people and verbs:
            subject = people[0]
            context = [re.sub(r"\s+", "", str(e)) for e in entities if str(e).strip() and str(e).strip() not in people]
            judged += 1
            record(_endorsed(sentences, subject, verbs, [c for c in context if c]))

    if not judged:
        return None, 0, ""
    return round(triggered / judged * 100.0, 2), triggered, " ‖ ".join(flagged)


def is_decline(answer: str, fact_recall: float | None) -> bool:
    """Declined: the answer says the records do not confirm it and states none of the reference facts."""
    return not fact_recall and any(marker in answer for marker in DECLINE_MARKERS)


def score_answer(item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """All automatic scores of one answer (one row of scores.csv, without the identifying columns)."""
    answer, contexts = result["answer"], result.get("contexts", [])
    question = item.get("query", "")
    recall, covered, total = evaluate_faithfulness(answer, item["gold_facts"], question)
    trap_rate, traps, flagged = evaluate_hallucination(answer, item["negative_traps"], item["category"],
                                                       item.get("target_entities"), question)
    return {
        "faithfulness": recall,
        "hallucination_rate": trap_rate,
        "declined": 100.0 if is_decline(answer, recall) else 0.0,
        **evaluate_retrieval_direct(item, contexts),
        **citation_stats(answer, contexts),
        "facts_covered": covered,
        "facts_total": total,
        "trap_flagged_text": flagged,
    }


# ── Citations ────────────────────────────────────────────────────────────────

# raw_<table>[-_ ]<rowid>; the controlled harness writes raw_bib_<rowid>, the deployed
# system raw_<table>-<rowid> (e.g. raw_event_info-45), so records are keyed by table and row.
ID_RE = re.compile(r"(?<![A-Za-z])raw_([a-z]+(?:_[a-z]+)*?)[_\-\s]?(\d+)\b", re.IGNORECASE)
ROW_PHRASE_RE = re.compile(r"(?<![A-Za-z])raw_([a-z]+(?:_[a-z]+)*) row (\d+)\b", re.IGNORECASE)


def record_key(table_suffix: str, rowid: str) -> str:
    table = table_suffix.lower()
    if table in ("bib", "bibliography"):
        table = "bibliography"
    return f"{table}:{int(rowid)}"


def _id_key(part: str) -> str | None:
    m = ID_RE.search(part)
    return record_key(m.group(1), m.group(2)) if m else None

# Things that look like citations but point at the system's own structure.
LABEL_RE = re.compile(
    r"""^(
        P\d+[a-z]?(?:_[A-Za-z0-9_]+)?        # CIDOC-CRM properties: P70_documents, P106i, P106i_02
      | E\d+(?:_[A-Za-z_]+)?                 # CIDOC-CRM classes
      | <?/?GRAPH_TOPOLOGY>? | GRAPH_\d+      # context block tags
      | <?/?SOURCE_EVIDENCE>?
      | S\d+ | g_\d+(?:\s*~\s*(?:g_)?\d+)?     # item labels and ranges of them (g_22~g_30)
      | graphrag_\d+(?:\s*~\s*\d+)?
      | 블록\s*\d+
      | graph\s*db(?:\s*컨텍스트)?
      | 그래프\s*db | 지식\s*그래프(?:\s*(?:문맥|컨텍스트|톱놀로지|토폴로지))?
      | 사료\s*및\s*지식\s*그래프
      | 액티비티 | activity
      | P14_carried_out_by | P7_took_place_at | P9_consists_of | ACTIVATED_AT | DEFINED_AS
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# Label fragments inside a longer citation ("사료 ID: P106i", "GRAPH_TOPOLOGY 구간 1").
LABEL_FRAGMENT_RE = re.compile(
    r"\bP\d+[a-z]?(?:_[A-Za-z0-9_]+)?\b|GRAPH_|SOURCE_EVIDENCE|\bg_\d+|\bgraphrag_\d+|그래프\s*(?:톱|토)폴로지|블록\s*\d|사료\s*ID\s*:",
    re.IGNORECASE,
)


def is_label(part: str) -> bool:
    return bool(LABEL_RE.match(part) or LABEL_FRAGMENT_RE.search(part))


GROUP_RE = re.compile(r"\(([^()\n]{1,200})\)|`([^`\n]{1,120})`")
SOURCE_CUE_RE = re.compile(r"\d|보$|보\s|신문|일보|판결|조서|문서|보고|사건|시위|배포|선언|회의|기록")


def _norm(text: str) -> str:
    return re.sub(r"[\s`\[\]『』「」《》<>\"'·.,:;~\-–—]", "", text).lower()


def context_ids(contexts: list[str]) -> set[str]:
    ids: set[str] = set()
    for ctx in contexts:
        for pattern in (ID_RE, ROW_PHRASE_RE):
            for table, rowid in pattern.findall(ctx):
                ids.add(record_key(table, rowid))
    return ids


def _split(group: str) -> list[str]:
    return [p.strip(" `[]") for p in re.split(r"[,;，、]|\s/\s", group) if p.strip(" `[]")]


# Evidence tags copied into an answer as citations: <SOURCE_EVIDENCE id="raw_source_info-12" entity="…"/>,
# or <SOURCE_EVIDENCE id="g_3">incident name</SOURCE_EVIDENCE>.
CITE_TAG_RE = re.compile(r"<SOURCE_EVIDENCE\b([^<>]*?)/?>(?:([^<>]{1,200})</SOURCE_EVIDENCE>)?", re.IGNORECASE)
INTERNAL_LABEL_RE = re.compile(r"(?<![A-Za-z0-9_])(?:g_\d+(?:\s*~\s*(?:g_)?\d+)?|graphrag_\d+(?:\s*~\s*\d+)?)(?![A-Za-z0-9_])")


def normalize_citation_tags(answer: str) -> str:
    """Replace an evidence tag by what it cites: its record id, else the incident name it wraps, else its label."""
    def rep(m: re.Match) -> str:
        found = re.search(r"\bid\s*=\s*\"?([^\"\s>/]+)", m.group(1) or "")
        ident = found.group(1) if found else ""
        inner = (m.group(2) or "").strip()
        if ident.lower().startswith("raw_"):
            return ident
        return inner or ident
    text = CITE_TAG_RE.sub(rep, answer or "")
    return re.sub(r"</?SOURCE_EVIDENCE[^>]*>", "", text, flags=re.IGNORECASE)


def verify_answer(answer: str, contexts: list[str]) -> list[dict[str, Any]]:
    """Return one entry per citation: text, kind, valid."""
    answer = normalize_citation_tags(answer)
    ids = context_ids(contexts)
    norm_ctx = _norm("\n".join(contexts))
    found: list[dict[str, Any]] = []
    for m in GROUP_RE.finditer(answer or ""):
        group = m.group(1) or m.group(2)
        parts = _split(group)
        has_pointer = any(ID_RE.search(p) or is_label(p) for p in parts)
        for part in parts:
            key = _id_key(part)
            if key:
                found.append({"text": part, "kind": "record_id", "valid": key in ids,
                              "span": m.span()})
            elif is_label(part):
                found.append({"text": part, "kind": "internal_label", "valid": False, "span": m.span()})
            elif (has_pointer or m.group(2)) and len(part) >= 4 or (len(part) >= 8 and SOURCE_CUE_RE.search(part)):
                found.append({"text": part, "kind": "named_source",
                              "valid": bool(_norm(part)) and _norm(part) in norm_ctx, "span": m.span()})
    return found


def clean_answer(answer: str, contexts: list[str]) -> str:
    """Remove record ids not in the evidence and internal labels; keep everything else."""
    ids = context_ids(contexts)

    def keep(part: str) -> bool:
        key = _id_key(part)
        if key:
            return key in ids
        return not is_label(part)

    def fix(m: re.Match) -> str:
        group = m.group(1) if m.group(1) is not None else m.group(2)
        parts = _split(group)
        if not any(ID_RE.search(p) or is_label(p) for p in parts):
            return m.group(0)  # not a citation group
        kept = [p for p in parts if keep(p)]
        if not kept:
            return ""
        return f"({', '.join(kept)})" if m.group(1) is not None else f"`{', '.join(kept)}`"

    cleaned = GROUP_RE.sub(fix, normalize_citation_tags(answer))
    cleaned = INTERNAL_LABEL_RE.sub("", cleaned)   # labels outside parentheses
    cleaned = re.sub(r"\(\s*(?:[,~]\s*)*\)", "", cleaned)
    return re.sub(r"[ \t]+([.,。])", r"\1", cleaned)


# ── Paired comparison of systems ─────────────────────────────────────────────

METRICS = ["faithfulness", "hallucination_rate", "declined", "entity_hit_rate_at_k", "entity_mrr"]
OVERALL = "Overall"
SOURCE_ORDER = ["curated", "template", "third_party"]


def load_records(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _is_failed(record: dict[str, Any]) -> bool:
    return str(record.get("generation_failed", "")).strip().lower() in {"1", "true"}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze(
    records: list[dict[str, Any]], group_field: str = "category",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Return (per-group rows, significance rows, bookkeeping counts).

    ``group_field`` is the CSV column the questions are broken down by:
    "category" (default) or "source". Overall is always included.
    """
    models = list(dict.fromkeys(r["model"] for r in records))
    # A question only enters the comparison if every model produced an answer
    # for it; otherwise an API failure would be scored as a model failure.
    failed_ids = {r["query_id"] for r in records if _is_failed(r)}
    seen: dict[str, set[str]] = defaultdict(set)
    for r in records:
        seen[r["query_id"]].add(r["model"])
    incomplete_ids = {qid for qid, ms in seen.items() if len(ms) < len(models)}
    excluded = failed_ids | incomplete_ids

    # scores[metric][model][query_id] = value
    scores: dict[str, dict[str, dict[str, float]]] = {m: defaultdict(dict) for m in METRICS}
    category_of: dict[str, str] = {}
    for r in records:
        if r["query_id"] in excluded:
            continue
        category_of[r["query_id"]] = (r.get(group_field) or "").strip() or "unknown"
        for metric in METRICS:
            value = _to_float(r.get(metric))
            if value is not None:
                scores[metric][r["model"]][r["query_id"]] = value

    groups = set(category_of.values())
    scopes = [OVERALL] + [s for s in SOURCE_ORDER if s in groups] + sorted(groups - set(SOURCE_ORDER))
    category_rows: list[dict[str, Any]] = []
    significance_rows: list[dict[str, Any]] = []

    for metric in METRICS:
        for scope in scopes:
            ids = sorted(q for q, c in category_of.items() if scope == OVERALL or c == scope)
            for model in models:
                values = [scores[metric][model][q] for q in ids if q in scores[metric][model]]
                mean, lo, hi = bootstrap_ci(values)
                category_rows.append({
                    "metric": metric, group_field: scope, "model": model, "n": len(values),
                    "mean": round(mean, 4), "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                })
            for model_a, model_b in itertools.combinations(models, 2):
                paired = [q for q in ids if q in scores[metric][model_a] and q in scores[metric][model_b]]
                a = [scores[metric][model_a][q] for q in paired]
                b = [scores[metric][model_b][q] for q in paired]
                diffs = [x - y for x, y in zip(a, b)]
                mean_diff, lo, hi = bootstrap_ci(diffs)
                significance_rows.append({
                    "metric": metric, group_field: scope, "model_a": model_a, "model_b": model_b,
                    "n_pairs": len(paired), "mean_diff_a_minus_b": round(mean_diff, 4),
                    "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                    "p_permutation": paired_permutation_test(a, b),
                })

    # Holm correction within each (metric, scope) family of model pairs.
    families: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in significance_rows:
        families[(row["metric"], row[group_field])].append(row)
    for rows in families.values():
        for row, adjusted in zip(rows, holm_adjust([r["p_permutation"] for r in rows])):
            row["p_holm"] = round(adjusted, 5)
            row["p_permutation"] = round(row["p_permutation"], 5)
            row["significant_0.05"] = bool(adjusted == adjusted and adjusted < 0.05)

    counts = {
        "questions_total": len(seen),
        "questions_used": len(category_of),
        "questions_excluded_generation_failed": len(failed_ids),
        "questions_excluded_incomplete": len(incomplete_ids - failed_ids),
    }
    return category_rows, significance_rows, counts
