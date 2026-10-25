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

from evaluation.benchmark import DATE_TOKEN_RE, is_generic_token, strip_josa
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
    return bool(re.search(
        r"지\s*않|지\s*못|아니|아니다|없[다습는었음]|확인되지|불가능|허위|허구|"
        r"사실과\s*다르|근거[가는]?\s*없|\b(?:not|never|cannot|unconfirmed|false)\b", text, re.I))


def evaluate_faithfulness(answer: str, gold_facts: list[str]) -> tuple[float, int, int]:
    """Check how many gold facts are covered accurately in the answer."""
    if not gold_facts:
        return 100.0, 0, 0
    covered = 0
    for fact in gold_facts:
        # Extract core keywords from fact
        keywords = [k for k in re.findall(r'[가-힣A-Za-z0-9]{2,}', fact) if k not in ['에서', '으로', '하고', '등이', '하여', '전개함', '받음', '형성됨']]
        # Keep the lexical proxy, but do not count an explicit denial as support.
        if any(_is_negative(sentence) == _is_negative(fact)
               and sum(kw in sentence for kw in keywords) >= max(1, (len(keywords) + 1) // 2)
               for sentence in _sentences(answer)):
            covered += 1
    score = (covered / len(gold_facts)) * 100.0
    return round(score, 2), covered, len(gold_facts)


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


def evaluate_hallucination(
    answer: str, negative_traps: list[str], category: str, entities: list[str] | None = None,
) -> tuple[float, int, str]:
    """Evaluate hallucination: Did the model falsely claim or endorse any negative traps?

    A trap counts when one answer sentence contains two of its keywords and the
    sentence has no rejection phrase. One keyword is always a person name or
    proper noun (the trap's leading noun or one of the question's entities);
    years, dates and province/county/city names alone never match. Returns
    (rate, traps triggered, the sentences that triggered them for manual review).
    """
    if not negative_traps:
        return 0.0, 0, ""
    hints = [str(e).strip() for e in entities or [] if str(e).strip() and not is_generic_token(str(e).strip())]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", answer) if s.strip()]
    traps_triggered = 0
    flagged: list[str] = []

    for trap in negative_traps:
        tokens = [strip_josa(k) for k in re.findall(r'[가-힣A-Za-z0-9]{2,}', trap) if k not in ['에서', '으로', '하고', '직접', '했다', '시작했다']]
        tokens = list(dict.fromkeys(t for t in tokens if not DATE_TOKEN_RE.fullmatch(t)))
        anchors = [t for i, t in enumerate(tokens)
                   if not is_generic_token(t) and (i == 0 or any(h in t or h.startswith(t) for h in hints))]
        if not anchors:
            continue  # a trap without a name or proper noun cannot be matched reliably
        # Prefer a second name/proper noun over an ordinary word as the other keyword.
        trap_keywords = [anchors[0]] + (anchors[1:] + [t for t in tokens if t not in anchors])[:1]
        hits = [s for s in sentences if all(kw in s for kw in trap_keywords) and not _is_negative(s)]
        if hits:
            traps_triggered += 1
            flagged += [s for s in hits if s not in flagged]

    rate = (traps_triggered / len(negative_traps)) * 100.0
    return round(rate, 2), traps_triggered, " ‖ ".join(flagged)


def is_decline(answer: str, fact_recall: float) -> bool:
    """Declined: the answer says the records do not confirm it and states none of the reference facts."""
    return fact_recall == 0 and any(marker in answer for marker in DECLINE_MARKERS)


def score_answer(item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """All automatic scores of one answer (one row of scores.csv, without the identifying columns)."""
    answer, contexts = result["answer"], result.get("contexts", [])
    recall, covered, total = evaluate_faithfulness(answer, item["gold_facts"])
    trap_rate, traps, flagged = evaluate_hallucination(answer, item["negative_traps"], item["category"], item.get("target_entities"))
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
      | S\d+ | g_\d+                         # item labels
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
    r"\bP\d+[a-z]?(?:_[A-Za-z0-9_]+)?\b|GRAPH_|SOURCE_EVIDENCE|그래프\s*(?:톱|토)폴로지|블록\s*\d|사료\s*ID\s*:",
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


def verify_answer(answer: str, contexts: list[str]) -> list[dict[str, Any]]:
    """Return one entry per citation: text, kind, valid."""
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

    cleaned = GROUP_RE.sub(fix, answer or "")
    return re.sub(r"[ \t]+([.,。])", r"\1", re.sub(r"\(\s*\)", "", cleaned))


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
