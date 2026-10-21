"""Evaluation report: report.md, LaTeX tables, and a console summary.

  python -m evaluation report              the latest run
  python -m evaluation report RUN [RUN..]  one run, or several runs combined (scores are averaged per
                                           question over the runs before testing, as in the paper)

The report collects what exists: automatic scores (scores.csv), context precision (judge.json),
the historians' answer ratings (rating.json), and the graph tables and expert validation in
results/graph/. Planned comparisons are paired sign-flip permutation tests over questions with
Holm correction within each metric.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pipeline.config import ROOT

import random

from evaluation.common import (
    GRAPH_RESULTS, RESULTS_DIR, add_sheet, bootstrap_ci, holm_adjust, new_workbook, paired_permutation_test, read_json, read_sheet,
)
from evaluation.metrics import METRICS, SOURCE_ORDER, analyze, verify_answer

LABEL = {
    "faithfulness": "Fact recall (%) ↑",
    "hallucination_rate": "Trap endorsement (%) ↓",
    "declined": "Declined (%)",
    "entity_hit_rate_at_k": "Entity hit rate ↑",
    "entity_mrr": "Entity MRR ↑",
}
# Planned comparisons: (research question, a, b). Rows are reported only when both systems ran.
CONTRASTS = [
    ("RQ1", "dcent", "passage"),
    ("RQ1", "dcent", "hybrid"),
    ("RQ1", "dcent", "graph"),
    ("RQ2 narrative paths", "dcent", "no-paths"),
    ("RQ2 query generation, record retrieval", "dcent", "controlled"),
    ("RQ2 two-stage generation (D-CENT evidence)", "controlled", "one-stage"),
    ("RQ2 two-stage generation (hybrid evidence)", "hybrid-two-stage", "hybrid"),
    ("RQ2 evidence (same generation)", "controlled", "hybrid-two-stage"),
]
TEST_METRICS = ["faithfulness", "hallucination_rate", "entity_hit_rate_at_k"]


# ── Loading ──────────────────────────────────────────────────────────────────
def _read_scores(run_dir: Path) -> list[dict[str, Any]]:
    with (run_dir / "scores.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["run"] = run_dir.name
        r["failed"] = str(r.get("generation_failed", "")).lower() in {"1", "true"}
        for k, v in list(r.items()):
            if k in METRICS or k.startswith("cite_") or k in ("context_chars", "prompt_tokens", "output_tokens", "latency_sec", "facts_covered", "facts_total"):
                try:
                    r[k] = float(v)
                except (TypeError, ValueError):
                    r[k] = None
    return rows


def _per_question(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per (system, question): metric means over the runs in which it did not fail."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["system"], r["query_id"])].append(r)
    merged = []
    for (_, _), rs in groups.items():
        ok = [r for r in rs if not r["failed"]] or rs
        rec = {k: rs[0][k] for k in ("query_id", "category", "source", "system", "model")}
        rec["generation_failed"] = all(r["failed"] for r in rs)
        for k in METRICS:
            vals = [r[k] for r in ok if r[k] is not None]
            rec[k] = sum(vals) / len(vals) if vals else None
        merged.append(rec)
    return merged


# ── Tables ───────────────────────────────────────────────────────────────────
def _rel(path: str) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return path



def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _fmt_ci(mean: float, lo: float, hi: float, digits: int = 1) -> str:
    if mean != mean:
        return "–"
    return f"{mean:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"


def _mean(values: list[float]) -> float:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else float("nan")


def _citations(rows: list[dict[str, Any]], systems: list[str]) -> list[list[str]]:
    out = []
    for s in systems:
        rs = [r for r in rows if r["system"] == s and not r["failed"]]
        valid = sum(r["cite_ids_valid"] or 0 for r in rs)
        ids = sum(r["cite_ids_total"] or 0 for r in rs)
        labels = sum(r["cite_internal_labels"] or 0 for r in rs)
        pointers = ids + labels
        no_pointer = sum(1 for r in rs if not (r["cite_ids_total"] or r["cite_internal_labels"]))
        out.append([s, len(rs), int(ids), int(valid), int(ids - valid), int(labels),
                    f"{valid / pointers:.2f}" if pointers else "–", f"{no_pointer / len(rs):.0%}" if rs else "–"])
    return out


def _contrasts(merged: list[dict[str, Any]], systems: list[str], labels: dict[str, str]) -> list[dict[str, Any]]:
    """Planned paired comparisons, Holm-corrected within each metric."""
    excluded = {r["query_id"] for r in merged if r["generation_failed"]}
    score = {(r["system"], r["query_id"]): r for r in merged if r["query_id"] not in excluded}
    out = []
    for metric in TEST_METRICS:
        family = []
        for rq, a, b in CONTRASTS:
            if a not in systems or b not in systems:
                continue
            qs = sorted(q for (s, q) in score if s == a and (b, q) in score
                        and score[(a, q)][metric] is not None and score[(b, q)][metric] is not None)
            xa = [score[(a, q)][metric] for q in qs]
            xb = [score[(b, q)][metric] for q in qs]
            diff, lo, hi = bootstrap_ci([x - y for x, y in zip(xa, xb)])
            family.append({"rq": rq, "metric": metric, "a": labels.get(a, a), "b": labels.get(b, b), "n": len(qs),
                           "diff": diff, "lo": lo, "hi": hi, "p": paired_permutation_test(xa, xb)})
        for row, adj in zip(family, holm_adjust([r["p"] for r in family])):
            row["p_holm"] = adj
        out += family
    return out


def _latex_main(table: list[list[str]], header: list[str], path: Path) -> None:
    def esc(text: str) -> str:
        return str(text).replace("%", r"\%").replace("_", r"\_").replace("&", r"\&").replace("↑", r"$\uparrow$").replace("↓", r"$\downarrow$")
    lines = [r"\begin{table*}[t]", r"\centering", r"\caption{Automatic measures by system (mean [95\% bootstrap CI])}",
             r"\label{tab:results}", r"\begin{tabular}{l" + "c" * (len(header) - 1) + "}", r"\toprule",
             " & ".join(rf"\textbf{{{esc(h)}}}" for h in header) + r" \\", r"\midrule"]
    lines += [" & ".join(esc(c) for c in row) + r" \\" for row in table]
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ── Report ───────────────────────────────────────────────────────────────────
def report(run_dirs: list[Path]) -> Path:
    run_dirs = sorted(run_dirs)
    configs = [read_json(d / "config.json") if (d / "config.json").exists() else {} for d in run_dirs]
    rows = [r for d in run_dirs for r in _read_scores(d)]
    if not rows:
        raise SystemExit("점수가 없습니다 (scores.csv가 비어 있음).")
    systems = list(dict.fromkeys(k for c in configs for k in c.get("systems", [])) or dict.fromkeys(r["system"] for r in rows))
    labels = {r["system"]: r["model"] for r in rows}
    merged = _per_question(rows)
    combined = len(run_dirs) > 1
    out_dir = RESULTS_DIR / f"combined_{run_dirs[0].name}_to_{run_dirs[-1].name}" if combined else run_dirs[0]
    out_dir.mkdir(parents=True, exist_ok=True)

    # analysis by category / source uses system labels as model names
    for r in merged:
        r["model"] = labels.get(r["system"], r["system"])
    by_cat, _, counts = analyze(merged, "category")
    has_source = any(r.get("source") for r in merged)
    by_src = analyze(merged, "source")[0] if has_source else []
    cell = {(r["metric"], r["category"], r["model"]): r for r in by_cat}

    questions = {r["query_id"]: r for r in merged}
    md: list[str] = []
    title = ", ".join(d.name for d in run_dirs)
    md.append(f"# 평가 리포트 — {title}\n")
    cfg = configs[-1]
    cond = cfg.get("conditions", {})
    md.append(_md_table(["항목", "값"], [
        ["실행", f"{len(run_dirs)}회 ({title})" + (" — 질문별로 실행 평균을 낸 뒤 검정" if combined else "")],
        ["벤치마크", f"{_rel(cfg.get('dataset', '?'))} — 질문 {len(questions)}개"],
        ["범주", ", ".join(f"{k} {v}" for k, v in Counter(q["category"] for q in questions.values()).most_common())],
        ["출처", ", ".join(f"{k} {v}" for k, v in Counter(q.get("source") or "unknown" for q in questions.values()).most_common())],
        ["생성 모델", cfg.get("generator", "?")],
        ["공통 조건", f"temperature {cond.get('temperature')}, top-p {cond.get('top_p')}, 근거 {cond.get('context_chars')}자, "
                   f"출력 {cond.get('answer_max_tokens')}토큰"],
        ["코드 버전", ", ".join(sorted({c.get('code_version') or '?' for c in configs}))],
        ["분석 대상", f"{counts['questions_used']}/{counts['questions_total']}문항 "
                  f"(생성 실패 제외 {counts['questions_excluded_generation_failed']}, 일부 시스템 누락 제외 {counts['questions_excluded_incomplete']})"],
    ]))

    # 1. Overall
    header = ["System"] + [LABEL[m] for m in METRICS] + ["Context chars", "Prompt tokens", "Latency (s)"]
    table = []
    for s in systems:
        lab = labels.get(s, s)
        raw = [r for r in rows if r["system"] == s and not r["failed"]]
        table.append([lab] + [_fmt_ci(cell[(m, "Overall", lab)]["mean"], cell[(m, "Overall", lab)]["ci95_low"],
                                      cell[(m, "Overall", lab)]["ci95_high"], 1 if m in ("faithfulness", "hallucination_rate", "declined") else 2)
                              if (m, "Overall", lab) in cell else "–" for m in METRICS]
                     + [f"{_mean([r['context_chars'] for r in raw]):.0f}", f"{_mean([r['prompt_tokens'] for r in raw]):.0f}",
                        f"{_mean([r['latency_sec'] for r in raw]):.1f}"])
    md.append("\n## 1. 시스템별 자동 지표 (평균 [95% 부트스트랩 CI])\n")
    md.append(_md_table(header, table))
    md.append("\n사실 재현율·함정 동의율·거절률은 어휘 일치에 기반한 근사치입니다. 함정 동의로 표시된 문장은 9절에서 직접 확인하십시오.")
    _latex_main(table, header, out_dir / "table_main.tex")

    # 2. Planned comparisons
    contrasts = _contrasts(merged, systems, labels)
    md.append("\n## 2. 계획된 비교 (쌍대 sign-flip 순열 검정, 지표별 Holm 보정)\n")
    for metric in TEST_METRICS:
        rows_m = [c for c in contrasts if c["metric"] == metric]
        if not rows_m:
            continue
        md.append(f"\n**{LABEL[metric]}**\n")
        md.append(_md_table(["비교", "A", "B", "n", "A−B [95% CI]", "p (Holm)", ""], [
            [c["rq"], c["a"], c["b"], c["n"], _fmt_ci(c["diff"], c["lo"], c["hi"], 2),
             f"{c['p_holm']:.4f}" if c["p_holm"] == c["p_holm"] else "–", "*" if c["p_holm"] < 0.05 else ""] for c in rows_m]))
    if not contrasts:
        md.append("비교할 시스템 쌍이 이 실행에 없습니다.")

    # 3. By category
    md.append("\n## 3. 범주별 사실 재현율 / 함정 동의율\n")
    cats = [c for c in dict.fromkeys(r["category"] for r in by_cat) if c != "Overall"]
    for metric in ("faithfulness", "hallucination_rate"):
        md.append(f"\n**{LABEL[metric]}**\n")
        md.append(_md_table(["Category", "n"] + [labels.get(s, s) for s in systems], [
            [c, cell.get((metric, c, labels.get(systems[0], systems[0])), {}).get("n", "")]
            + [f"{cell[(metric, c, labels.get(s, s))]['mean']:.1f}" if (metric, c, labels.get(s, s)) in cell else "–" for s in systems]
            for c in cats]))
    _latex_by_category(by_cat, systems, labels, out_dir / "table_by_category.tex")

    # 4. By source
    if by_src:
        md.append("\n## 4. 질문 출처별 사실 재현율\n")
        md.append("저자 작성(curated)·외부 작성(third_party) 문항은 시스템을 아는 사람이 쓴 것인지 여부가, 템플릿(template) 문항은 그래프와 같은 사건 대장에서 나왔다는 점이 다릅니다.\n")
        scell = {(r["metric"], r["source"], r["model"]): r for r in by_src}
        srcs = [s for s in SOURCE_ORDER if any(k[1] == s for k in scell)] + sorted({k[1] for k in scell} - set(SOURCE_ORDER) - {"Overall"})
        md.append(_md_table(["Source", "n"] + [labels.get(s, s) for s in systems], [
            [src, scell.get(("faithfulness", src, labels.get(systems[0], systems[0])), {}).get("n", "")]
            + [_fmt_ci(scell[k]["mean"], scell[k]["ci95_low"], scell[k]["ci95_high"]) if (k := ("faithfulness", src, labels.get(s, s))) in scell else "–"
               for s in systems] for src in srcs]))

    # 5. Citations
    md.append("\n## 5. 인용 (검증기 적용 전 답변 기준)\n")
    md.append(_md_table(["System", "답변", "기록 ID 인용", "유효", "무효", "내부 표지", "포인터 유효율", "포인터 없는 답변"],
                        [[labels.get(r[0], r[0])] + r[1:] for r in _citations(rows, systems)]))
    md.append("\n포인터 유효율 = 근거에 실제로 있는 기록 ID 인용 / (기록 ID 인용 + 내부 표지). 인용이 문장을 뒷받침하는지는 전문가 평가(6절)로 판단합니다.")

    # 6. Secondary and human measures
    md.append("\n## 6. 보조 지표와 전문가 평가\n")
    judged = [d for d in run_dirs if (d / "judge.json").exists()]
    if judged:
        res = [x for d in judged for x in read_json(d / "judge.json")["results"]]
        md.append(f"**Context precision** (판정 모델 {read_json(judged[0] / 'judge.json')['judge_model']})\n")
        md.append(_md_table(["System", "n", "평균 [95% CI]"], [
            [labels.get(s, s), len(v), _fmt_ci(*bootstrap_ci(v), 3)]
            for s in systems if (v := [x["context_precision"] for x in res if x["system"] == s])]))
    else:
        md.append("- Context precision: 아직 없음 (`python -m evaluation judge`).")
    rated = [d for d in run_dirs if (d / "rating.json").exists()]
    if rated:
        rating = read_json(rated[-1] / "rating.json")
        metrics = ["accuracy", "completeness", "usefulness", "narrative_order", "narrative_attribution", "unsupported_claims", "declined"]
        md.append(f"\n**답변 블라인드 평가** (평가자 {rating['n_raters']}명, 질문 {rating['n_questions']}개)\n")
        md.append(_md_table(["System"] + metrics + ["인용 정확도(엄격)"], [
            [m] + [f"{v[k]['mean']:.2f}" if v.get(k, {}).get("mean") is not None else "–" for k in metrics]
            + [f"{v['citation_accuracy']['strict']:.2f}" if v.get("citation_accuracy", {}).get("strict") is not None else "–"]
            for m, v in rating["models"].items()]))
        for pair, entry in rating.get("agreement", {}).items():
            md.append(f"\n평가자 일치도 {pair}: " + ", ".join(
                f"{k} κ={v.get('weighted_kappa', v.get('kappa')):.2f}" for k, v in entry.items()
                if v.get("weighted_kappa", v.get("kappa")) is not None))
    else:
        md.append("- 답변 블라인드 평가: 아직 없음 (`python -m evaluation rate export` → 평가 → `rate score`).")

    # 7. Graph
    md.append("\n## 7. 그래프\n")
    md.append(_graph_section())

    # 8. Run-to-run variation
    if combined:
        md.append("\n## 8. 실행 간 변동 (사실 재현율 평균)\n")
        md.append(_md_table(["System"] + [d.name for d in run_dirs], [
            [labels.get(s, s)] + [f"{_mean([r['faithfulness'] for r in rows if r['system'] == s and r['run'] == d.name and not r['failed']]):.1f}"
                                  for d in run_dirs] for s in systems]))

    # 9. Items to check by hand
    md.append("\n## 9. 직접 확인할 항목\n")
    failed = [r for r in rows if r["failed"]]
    md.append(f"**생성 실패** {len(failed)}건" + (": " + ", ".join(f"{r['query_id']}/{r['system']}" for r in failed[:30]) if failed else ""))
    flagged = [r for r in rows if (r.get("trap_flagged_text") or "").strip()]
    md.append(f"\n**함정 동의로 표시된 문장** {len(flagged)}건 (부정 표현 없이 함정의 고유명 두 개가 한 문장에 나온 경우)\n")
    for r in flagged[:40]:
        md.append(f"- `{r['query_id']}` {labels.get(r['system'], r['system'])}: {r['trap_flagged_text'][:200]}")

    # 10. Worked example and error analysis (from the latest run)
    md.append("\n## 10. 사례와 오류 분석\n")
    case = write_case_study(run_dirs[-1], labels)
    md.append(f"- 사례: `{case.name}` — 질문 하나에 대한 D-CENT의 서사 경로·팩트 표·답변과 기준선 답변, 인용 검증 결과."
              if case else "- 사례: D-CENT(end-to-end) 답변이 없어 만들지 않았습니다.")
    md.append(_error_section(run_dirs[-1]))

    path = out_dir / "report.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    _print_summary(title, table, header, contrasts, path)
    return path


def _latex_by_category(by_cat: list[dict[str, Any]], systems: list[str], labels: dict[str, str], path: Path) -> None:
    cell = {(r["category"], r["model"]): r for r in by_cat if r["metric"] == "faithfulness"}
    cats = list(dict.fromkeys(r["category"] for r in by_cat))
    names = [labels.get(s, s) for s in systems]
    lines = [r"\begin{table*}[t]", r"\centering", r"\caption{Fact recall by question category (mean [95\% bootstrap CI])}",
             r"\label{tab:by_category}", r"\begin{tabular}{lc" + "c" * len(names) + "}", r"\toprule",
             r"\textbf{Category} & \textbf{N} & " + " & ".join(rf"\textbf{{{n}}}" for n in names) + r" \\", r"\midrule"]
    for c in cats:
        n = cell.get((c, names[0]), {}).get("n", "")
        vals = [f"{cell[(c, m)]['mean']:.1f} [{cell[(c, m)]['ci95_low']:.1f}, {cell[(c, m)]['ci95_high']:.1f}]" if (c, m) in cell else "--" for m in names]
        lines.append(f"{c.replace('&', chr(92) + '&')} & {n} & " + " & ".join(vals) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _graph_section() -> str:
    parts = []
    nodes, rels = GRAPH_RESULTS / "node_counts.csv", GRAPH_RESULTS / "relationship_counts.csv"
    if nodes.exists() and rels.exists():
        def read(p: Path) -> list[list[str]]:
            with p.open(encoding="utf-8-sig") as f:
                return list(csv.reader(f))[1:]
        n_rows, r_rows = read(nodes), read(rels)
        total_n = next((r[1] for r in n_rows if r[0] == "TOTAL"), "?")
        total_r = next((r[1] for r in r_rows if r[0] == "TOTAL"), "?")
        parts.append(f"노드 {int(total_n):,}개, 관계 {int(total_r):,}개 (`{nodes.parent.name}/`).\n")
        parts.append(_md_table(["관계 유형", "개수"], [[t, f"{int(c):,}"] for t, c in r_rows if t != "TOTAL"][:12]))
    else:
        parts.append("- 그래프 규모: 아직 없음 (`python -m pipeline inspect counts`).")
    sp = GRAPH_RESULTS / "sparsity.json"
    if sp.exists():
        real = read_json(sp).get("real_events", {})
        if real.get("nodes"):
            parts.append(f"\n사건 {real['nodes']:,}개 중 인물 미연결 {real['without_person']:,}개 "
                         f"({real['without_person'] / real['nodes']:.1%}), 장소 미연결 {real['without_place']:,}개, 고립 {real['isolated']:,}개.")
    ex = GRAPH_RESULTS / "expert.json"
    if ex.exists():
        e = read_json(ex)
        parts.append("\n**전문가 검증** (`expert.json`)\n")
        rows = []
        for name, ann in e.get("annotators", {}).items():
            for t, v in ann.get("scores", {}).items():
                rows.append([name, t] + [f"{v[k]:.2f}" if isinstance(v.get(k), (int, float)) else "–" for k in ("precision", "recall", "f1")]
                            + [f"{v['tp']}/{v['fp']}/{v['fn']}"])
        parts.append(_md_table(["검증자", "유형", "Precision", "Recall", "F1", "TP/FP/FN"], rows) if rows else "채점 결과가 비어 있습니다.")
        for pair, v in e.get("agreement", {}).items():
            parts.append(f"\n검증자 일치도 {pair}: 공통 {v['n_items']}개, Cohen's κ {v['cohen_kappa']}")
    else:
        parts.append("\n- 전문가 검증: 아직 없음 (`python -m evaluation expert export` → 검증 → `expert score`).")
    return "\n".join(parts)


def _print_summary(title: str, table: list[list[str]], header: list[str], contrasts: list[dict[str, Any]], path: Path) -> None:
    print(f"\n📊 {title}")
    width = max(len(r[0]) for r in table) + 2
    print(f"{'System':<{width}}" + "".join(f"{h[:22]:>26}" for h in header[1:4]))
    for row in table:
        print(f"{row[0]:<{width}}" + "".join(f"{c:>26}" for c in row[1:4]))
    shown = [c for c in contrasts if c["metric"] == "faithfulness"]
    if shown:
        print("\n사실 재현율 비교 (A−B, Holm 보정 p):")
        for c in shown:
            print(f"  {c['a']} vs {c['b']}: {c['diff']:+.1f} (n={c['n']}, p={c['p_holm']:.4f}){' *' if c['p_holm'] < 0.05 else ''}")
    print(f"\n📄 {path}")


# ── Worked example ───────────────────────────────────────────────────────────
NARRATIVE_CATEGORIES = ("Spatial-Temporal Diffusion", "Multi-hop Chaining")


def write_case_study(run_dir: Path, labels: dict[str, str]) -> Path | None:
    """case_study.md: one narrative question, D-CENT end to end next to the best-scoring baseline."""
    answers = [a for a in read_json(run_dir / "answers.json") if not a.get("generation_failed")]
    scores = {}
    with (run_dir / "scores.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            scores[(r["query_id"], r["system"])] = r
    candidates = [a for a in answers if a["system"] == "dcent" and a["category"] in NARRATIVE_CATEGORIES
                  and str(a.get("narrative_used")).lower() == "true"] or [a for a in answers if a["system"] == "dcent"]
    if not candidates:
        return None

    def recall(a: dict[str, Any]) -> float:
        return float(scores.get((a["query_id"], a["system"]), {}).get("faithfulness") or 0)

    case = max(candidates, key=lambda a: (recall(a), -len(a["response"])))
    others = [a for a in answers if a["query_id"] == case["query_id"] and a["system"] in ("passage", "hybrid", "graph")]
    baseline = max(others, key=recall) if others else None

    def cites(a: dict[str, Any]) -> str:
        found = verify_answer(a["response"], a["retrieved_contexts"])
        if not found:
            return "인용 없음"
        return "; ".join(f"{c['text']} ({'유효' if c['valid'] else '무효'}, {c['kind']})" for c in found[:20])

    context = (case["retrieved_contexts"] or [""])[0]
    lines = [f"# 사례: {case['query_id']} ({case['category']})", "", f"**질문** {case['user_input']}", "",
             "**기준 사실**", "", *[f"- {line}" for line in case["reference"].split("\n")], "",
             "## D-CENT (end-to-end)", "", f"사실 재현율 {recall(case):.0f}%", "",
             "**생성 문맥 앞부분 (서사 경로가 맨 앞)**", "", "```text", context[:2500], "```", "",
             "**팩트 표**", "", case.get("fact_table") or "(없음)", "", "**답변**", "", case["response"], "",
             f"**인용 검증** {cites(case)}", ""]
    if baseline:
        lines += [f"## {labels.get(baseline['system'], baseline['system'])}", "", f"사실 재현율 {recall(baseline):.0f}%", "",
                  "**답변**", "", baseline["response"], "", f"**인용 검증** {cites(baseline)}", ""]
    path = run_dir / "case_study.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── Error analysis ───────────────────────────────────────────────────────────
FAILURE_MODES = ["misattribution", "unsupported", "conflation", "misordering"]
ERROR_SAMPLE = 20
ERROR_INSTRUCTIONS = [
    "D-CENT 답변 오류 분석 시트",
    "",
    "각 답변을 근거(evidence)와 대조해, 논문이 정의한 네 가지 실패가 있으면 해당 칸에 Y, 없으면 N을 적습니다.",
    "  misattribution: 서로 다른 사건·기록의 인물·장소·행위를 하나로 합침",
    "  unsupported: 근거에 없는 내용을 서술함 (모델의 사전 지식으로 채움)",
    "  conflation: 동명이인이나 같은 이름의 다른 마을을 같은 것으로 취급함",
    "  misordering: 사건의 시간 순서를 잘못 배열함",
    "note에는 오류가 난 문장과 원인(검색 실패, 그래프 누락, 생성 오류 등)을 적어 주십시오.",
]


def _error_section(run_dir: Path) -> str:
    """Create error_analysis.xlsx once (seeded sample of D-CENT answers); summarise it when coded."""
    path = run_dir / "error_analysis.xlsx"
    if not path.exists():
        answers = [a for a in read_json(run_dir / "answers.json") if a["system"] == "dcent" and not a.get("generation_failed")]
        if not answers:
            return "- 오류 분석: D-CENT(end-to-end) 답변이 없어 시트를 만들지 않았습니다."
        rng = random.Random(42)
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for a in answers:
            by_cat[a["category"]].append(a)
        for group in by_cat.values():
            rng.shuffle(group)
        picked = [a for row in zip(*[g + [None] * ERROR_SAMPLE for g in by_cat.values()]) for a in row if a][:ERROR_SAMPLE]
        rows = [{"query_id": a["query_id"], "category": a["category"], "question": a["user_input"], "reference": a["reference"],
                 "answer": a["response"], "evidence": (a["retrieved_contexts"] or [""])[0][:3000]} for a in picked]
        wb = new_workbook(ERROR_INSTRUCTIONS)
        add_sheet(wb, "answers", ["query_id", "category", "question", "reference", "answer", "evidence", *FAILURE_MODES, "note"], rows,
                  widths={"question": 40, "reference": 50, "answer": 80, "evidence": 80, "note": 40},
                  choices={m: ["Y", "N"] for m in FAILURE_MODES})
        wb.save(path)
        return f"- 오류 분석: `{path.name}`에 D-CENT 답변 {len(rows)}개를 범주별로 뽑았습니다. 네 가지 실패 유형을 표시한 뒤 `report`를 다시 실행하면 집계가 여기에 나옵니다."
    rows = read_sheet(path, "answers")
    coded = [r for r in rows if any(r.get(m, "").upper() in ("Y", "N") for m in FAILURE_MODES)]
    if not coded:
        return f"- 오류 분석: `{path.name}`가 아직 채워지지 않았습니다."
    counts = {m: sum(r.get(m, "").upper() == "Y" for r in coded) for m in FAILURE_MODES}
    any_error = sum(any(r.get(m, "").upper() == "Y" for m in FAILURE_MODES) for r in coded)
    table = _md_table(["실패 유형", "답변 수", "비율"], [[m, n, f"{n / len(coded):.0%}"] for m, n in counts.items()]
                      + [["하나 이상", any_error, f"{any_error / len(coded):.0%}"]])
    return f"**오류 분석** (D-CENT 답변 {len(coded)}개, `{path.name}`)\n\n" + table
