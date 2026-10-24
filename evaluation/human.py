"""Judgments by historians, through Excel workbooks.

  expert export   seeded random sample of 150 records; one workbook per annotator (A, B) listing
                  everything the pipeline extracted, to be marked O/X, plus what it missed
  expert score    precision / recall / F1 with bootstrap intervals and Cohen's kappa
                  -> results/graph/expert.json
  rate export     blind rating of a run's answers: one workbook per rater, system names replaced
                  by shuffled labels; the key goes to annotation/rating_key/, never to raters
  rate score      per-system means with intervals, paired tests, citation accuracy, agreement,
                  and the correlation of ratings with answer length -> <run>/rating.json

Citation accuracy backs the provenance claim: for every sentence that cites a record,
the rater reads the record and marks whether it supports the sentence.
"""

from __future__ import annotations

import html
import itertools
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from pipeline.config import SOURCE_ID_COLUMNS, get_pg_connection
from pipeline.text import extract_ruby_parts

from evaluation.common import (
    EXPERT_DIR, GRAPH_RESULTS, RATING_DIR, RATING_KEY_DIR,
    add_sheet, bootstrap_ci, cohen_kappa, gwet_ac1, holm_adjust, new_workbook, paired_permutation_test, prf, read_json, read_sheet, write_json,
)
from evaluation.metrics import ID_RE, record_key

# ═════════════════════════════════════════════════════════════════════════════
# Expert validation of extracted entities and relations
# ═════════════════════════════════════════════════════════════════════════════


# Sample of 150 records, stratified by table: (table, column with the event id or None, records, filter).
# Source entries linked to an incident get the largest share, because there a person named in the
# record may be a participant or only a witness or official, which is what the relation judgments test.
SAMPLE_PLAN = [
    ("raw_event_info", "아이디", 30, None),
    ("raw_source_info", "사건아이디", 60, """"사건아이디" IS NOT NULL AND btrim("사건아이디") <> ''"""),
    ("raw_bibliography", None, 30, None),
    ("raw_detail_place", None, 30, None),
]
TYPES = ["Person", "Place", "P14_carried_out_by", "P7_took_place_at"]
ENTITY_TYPES = {"Person", "Place"}
RELATION_TYPES = [t for t in TYPES if t not in ENTITY_TYPES]
# Missed items are collected for entities only: writing a missed relation would require the annotator to
# reconstruct the incident, which few do, so relation recall would be overstated. Relations get precision only.
MISSED_TYPES = ["Person", "Place"]
SKIP_COLUMNS = {"제공", "제공일자", "tei_status"}   # administrative columns, not part of the record
VERDICT_OK, VERDICT_WRONG = "O", "X"

EXPERT_INSTRUCTIONS = [
    "개체·관계 추출 전문가 검증 시트",
    "",
    "목적: 지식그래프가 사료에서 뽑아낸 인명·지명과 사건 관계가 맞는지, 무엇을 놓쳤는지 측정합니다 (논문 RQ3).",
    "사료 150건, 예상 소요 시간 4~6시간. 여러 번에 나누어 작업해도 됩니다.",
    "",
    "1. [records] 시트에서 사료 원문(text)을 읽습니다. 데이터베이스 표의 한 행이며, 한 줄에 '열 이름: 값' 하나씩 표시합니다.",
    "   한자는 원문 그대로이고, 파이프라인이 붙인 독음은 지웠습니다.",
    "   event_title은 이 사료가 연결된 사건입니다. 비어 있으면 사건에 연결되지 않은 사료입니다.",
    "   source_id는 삼일운동 데이터베이스의 식별자로, 원문을 데이터베이스에서 직접 확인할 때 씁니다.",
    "2. [extracted] 시트는 파이프라인이 해당 사료에서 추출한 인명·지명·관계입니다. 각 행의 verdict에 O(맞음) 또는 X(틀림)를 기입합니다.",
    "   - Person: 실제 사람 이름이면 O. 기관·직책·지명·일반명사이거나 이름의 경계가 틀리면 X.",
    "     예) O: 金容圭 (김용규)   X: 巡査 (순사, 직책)   X: 金容 (김용, 이름이 잘림)",
    "     괄호 안 한글은 파이프라인이 붙인 독음입니다. 이름은 맞지만 독음이 틀리면 X로 하고 corrected에 올바른 독음을 적습니다.",
    "     예) X: 車相晋 (거상진) → corrected: 차상진",
    "     일본인 이름(예: 塚原友太郞)은 사람 이름이면 O로 하고 note에 '일본인'이라고 적습니다. 한국식 독음은 판정하지 않습니다.",
    "     단체명(예: 大韓人國民會)과 직함(예: 內閣總理大臣)은 X입니다.",
    "   - Place: 장소를 가리키면 O. 세부장소 표에 등록된 주재소·관청·학교 같은 시설도 장소로 보아 O입니다.",
    "     예) O: 水原郡, 蘭谷憲兵駐在所   X: 道內 1곳 (장소가 아님), 이름의 일부만 잘린 경우",
    "   - P14_carried_out_by: 그 인물이 해당 사건의 행위자·참여자로 사료에 기록되어 있으면 O.",
    "     단순 언급(증인, 수신자, 재판관, 순사, 피해자, 가족 등)이면 X이고 note에 역할을 적어 주십시오. 예) note: 증인",
    "   - P7_took_place_at: 그 사건이 해당 장소에서 일어난 것으로 사료에 기록되어 있으면 O.",
    "     예) X: 시위는 송산면에서 일어났고 水原郡 경찰서는 체포자를 보낸 곳일 뿐인 경우.",
    "   - X인 경우 corrected에 올바른 값을 적어 주시면 오류 유형 분석에 사용합니다 (선택).",
    "   - 사료만으로 판단할 수 없으면 verdict를 비워 두고 note에 이유를 적습니다. 빈 칸은 계산에서 제외됩니다.",
    "3. [missed] 시트에는 사료에 있는데 파이프라인이 놓친 인명·지명을 한 행에 하나씩 추가합니다 (record_id, type, value).",
    "   관계는 놓친 것을 적지 않습니다. 관계는 추출된 항목이 맞는지만 판정합니다.",
    "   추출 항목이 하나도 없는 사료도 반드시 읽고, 놓친 것이 있으면 적어 주십시오.",
    "   예) record_id: raw_bibliography:123, type: Person, value: 李東輝",
    "4. 다른 검증자와 상의하지 말고 독립적으로 판정해 주십시오. item_id와 record_id는 수정하지 마십시오.",
]


def _surface(inner: str) -> str:
    """Readable form of a tagged span, e.g. '金容圭 (김용규)'."""
    foreign, gloss = extract_ruby_parts(inner)
    if foreign and gloss:
        return f"{foreign} ({gloss})"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()


def _body_text(tei: str) -> str:
    """The record as 'column: value' lines, glosses dropped so the annotator reads the source as written."""
    body = re.search(r"<body>(.*?)</body>", tei or "", flags=re.DOTALL)
    body = re.sub(r"<rt[^>]*>[^<]*</rt>", "", body.group(1) if body else tei or "")
    lines = []
    # An empty column is serialized as a self-closing <p data-col="…"/>; match it as empty,
    # or the next column's value would be shown under its name.
    for col, inner in re.findall(r'<p\b[^>]*?data-col="([^"]*)"[^>]*?(?:/>|>(.*?)</p>)', body, flags=re.DOTALL):
        value = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner))).strip()
        if value and col not in SKIP_COLUMNS:
            lines.append(f"{html.unescape(col)}: {value}")
    if lines:
        return "\n".join(lines)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()   # TEI without column labels


def _refuse_overwrite(paths: list[Path]) -> None:
    """Workbooks may already hold judgments; never overwrite them silently."""
    existing = [p for p in paths if p.exists()]
    if existing:
        raise SystemExit("이미 있는 워크북을 덮어쓰지 않습니다. 채점이 끝났거나 필요 없으면 다른 폴더로 옮긴 뒤 다시 실행하세요:\n  "
                         + "\n  ".join(str(p) for p in existing))


def sample_records(n: int, seed: int) -> list[dict[str, Any]]:
    conn = get_pg_connection()
    cur = conn.cursor()
    cur.execute('SELECT "아이디", "사건명" FROM raw_event_info')
    event_titles = {str(i): str(t or "").strip() for i, t in cur.fetchall()}

    planned = sum(k for _, _, k, _ in SAMPLE_PLAN)
    records = []
    for table, event_col, share, condition in SAMPLE_PLAN:
        quota = round(share * n / planned)
        event_expr = f'"{event_col}"' if event_col else "NULL"
        source_col = SOURCE_ID_COLUMNS.get(table)
        source_expr = f'"{source_col}"' if source_col else "NULL"
        where = "tei IS NOT NULL" + (f" AND {condition}" if condition else "")
        cur.execute(
            f'SELECT rowid, tei, {event_expr}, {source_expr} FROM "{table}" WHERE {where} '
            f"ORDER BY md5(rowid::text || %s) LIMIT %s",
            (str(seed), quota),
        )
        for rowid, tei, event_id, source_id in cur.fetchall():
            event_id = str(event_id).strip() if event_id else ""
            records.append({
                "record_id": f"{table}:{rowid}",
                "source_id": str(source_id or "").strip(),
                "table": table,
                "event_id": event_id,
                "event_title": event_titles.get(event_id, ""),
                "tei": tei,
            })
    cur.close()
    conn.close()
    return records[:n]


def extract_items(record: dict[str, Any]) -> list[dict[str, str]]:
    """Entities tagged in the TEI and the event relations the graph builder derives from them."""
    tei = record["tei"]
    persons = list(dict.fromkeys(_surface(m) for m in re.findall(r"<persName[^>]*>(.*?)</persName>", tei, flags=re.DOTALL)))
    places = list(dict.fromkeys(_surface(m) for m in re.findall(r"<placeName[^>]*>(.*?)</placeName>", tei, flags=re.DOTALL)))
    items = [{"type": "Person", "value": p} for p in persons if p]
    items += [{"type": "Place", "value": p} for p in places if p]
    if record["event_id"]:
        event = record["event_title"] or record["event_id"]
        items += [{"type": "P14_carried_out_by", "value": f"[{event}] → {p}"} for p in persons if p]
        items += [{"type": "P7_took_place_at", "value": f"[{event}] → {p}"} for p in places if p]
    return items


def expert_export(n: int = 150, seed: int = 42, annotators: tuple[str, ...] = ("A", "B")) -> int:
    """Write one annotation workbook per annotator to annotation/expert/."""
    out_dir = EXPERT_DIR
    _refuse_overwrite([out_dir / f"annotation_{a}.xlsx" for a in annotators])
    records = sample_records(n, seed)
    record_rows, item_rows = [], []
    for record in records:
        record_rows.append({
            "record_id": record["record_id"], "source_id": record.get("source_id", ""), "table": record["table"],
            "event_title": record["event_title"], "text": _body_text(record["tei"]),
        })
        for n, item in enumerate(extract_items(record), 1):
            item_rows.append({
                "item_id": f"{record['record_id']}/{n}", "record_id": record["record_id"],
                "kind": "entity" if item["type"] in ENTITY_TYPES else "relation", **item,
            })

    out_dir.mkdir(parents=True, exist_ok=True)
    for annotator in annotators:
        wb = new_workbook(EXPERT_INSTRUCTIONS)
        add_sheet(wb, "records", ["record_id", "source_id", "table", "event_title", "text"], record_rows,
                  widths={"record_id": 26, "source_id": 18, "event_title": 34, "text": 110})
        add_sheet(wb, "extracted", ["item_id", "record_id", "kind", "type", "value", "verdict", "corrected", "note"], item_rows,
                  widths={"item_id": 28, "record_id": 26, "type": 22, "value": 46, "corrected": 30, "note": 30},
                  choices={"verdict": [VERDICT_OK, VERDICT_WRONG]})
        add_sheet(wb, "missed", ["record_id", "type", "value", "note"], [],
                  widths={"record_id": 26, "type": 22, "value": 46, "note": 30}, choices={"type": MISSED_TYPES})
        path = out_dir / f"annotation_{annotator}.xlsx"
        wb.save(path)
        print(f"📝 {path}")

    manifest = {
        "seed": seed, "n_records": len(records), "n_items": len(item_rows),
        "records_without_extraction": sum(1 for r in records if not extract_items(r)),
        "record_ids": [r["record_id"] for r in records],
    }
    (out_dir / "sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 사료 {len(records)}건, 추출 항목 {len(item_rows)}개 (추출 0건인 사료 {manifest['records_without_extraction']}건 포함)")
    return 0


def _load_annotation(path: Path) -> dict[str, Any]:
    verdicts = {}
    unjudged = 0
    counts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0}))
    for row in read_sheet(path, "extracted"):
        verdict = row["verdict"].upper()
        if verdict not in (VERDICT_OK, VERDICT_WRONG):
            unjudged += 1
            continue
        verdicts[row["item_id"]] = verdict
        counts[row["type"]][row["record_id"]]["tp" if verdict == VERDICT_OK else "fp"] += 1
    ignored_missed = 0
    for row in read_sheet(path, "missed"):
        if row["type"] in RELATION_TYPES:
            ignored_missed += 1          # relations are scored for precision only
            continue
        if row["type"] not in MISSED_TYPES:
            raise ValueError(f"{path} [missed]: 알 수 없는 type '{row['type']}' (record {row['record_id']})")
        counts[row["type"]][row["record_id"]]["fn"] += 1
    records = [r["record_id"] for r in read_sheet(path, "records")]
    return {"verdicts": verdicts, "counts": counts, "records": records, "unjudged": unjudged, "ignored_missed": ignored_missed}


def _score_type(per_record: dict[str, dict[str, int]], records: list[str], n_boot: int = 5000) -> dict[str, Any]:
    """Micro P/R/F1 with a bootstrap CI that resamples source records."""
    table = np.asarray([[per_record[r]["tp"], per_record[r]["fp"], per_record[r]["fn"]] if r in per_record else [0, 0, 0]
                        for r in records])
    tp, fp, fn = (int(x) for x in table.sum(axis=0))
    precision, recall, f1 = prf(tp, fp, fn)
    rng = np.random.default_rng(0)
    sums = table[rng.integers(0, len(records), size=(n_boot, len(records)))].sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        boot_p = sums[:, 0] / (sums[:, 0] + sums[:, 1])
        boot_r = sums[:, 0] / (sums[:, 0] + sums[:, 2])

    def interval(samples: np.ndarray) -> list[float | None]:
        samples = samples[~np.isnan(samples)]
        return [round(float(q), 4) for q in np.quantile(samples, [0.025, 0.975])] if samples.size else [None, None]

    def clean(value: float) -> float | None:
        return None if value != value else round(value, 4)

    return {"tp": tp, "fp": fp, "fn": fn, "precision": clean(precision), "precision_ci95": interval(boot_p),
            "recall": clean(recall), "recall_ci95": interval(boot_r), "f1": clean(f1)}


def _precision_only_for_relations(scores: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Missed relations are not collected, so relation recall and F1 are not estimated."""
    for t in [*RELATION_TYPES, "Relation (all)"]:
        if t in scores:
            scores[t].update(recall=None, recall_ci95=[None, None], f1=None)
    return scores


def expert_score(files: list[Path]) -> int:
    """Score completed annotation workbooks (default: every annotation_*.xlsx in annotation/expert/)."""
    files = files or sorted(EXPERT_DIR.glob("annotation_*.xlsx"))
    if not files:
        raise SystemExit(f"채점할 워크북이 없습니다: {EXPERT_DIR}")
    output = GRAPH_RESULTS / "expert.json"
    annotations = {path.stem: _load_annotation(path) for path in files}
    report: dict[str, Any] = {"annotators": {}, "agreement": {}}
    fmt = lambda v: "   n/a" if v is None else f"{v * 100:6.1f}"
    for name, ann in annotations.items():
        merged = {"Entity (all)": defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0}),
                  "Relation (all)": defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})}
        for type_name in TYPES:
            group = "Entity (all)" if type_name in ENTITY_TYPES else "Relation (all)"
            for record_id, c in ann["counts"][type_name].items():
                for key in c:
                    merged[group][record_id][key] += c[key]
        per_type = {**{t: ann["counts"][t] for t in TYPES}, **merged}
        scores = _precision_only_for_relations(
            {t: _score_type(per_record, ann["records"]) for t, per_record in per_type.items()})

        # The same scores restricted to the records of one source table.
        tables = list(dict.fromkeys(r.rsplit(":", 1)[0] for r in ann["records"]))
        by_table = {}
        for table in tables:
            table_records = [r for r in ann["records"] if r.rsplit(":", 1)[0] == table]
            by_table[table] = {"n_records": len(table_records),
                               "scores": _precision_only_for_relations(
                                   {t: _score_type(per_record, table_records) for t, per_record in per_type.items()})}

        report["annotators"][name] = {
            "n_records": len(ann["records"]),
            "n_records_by_table": {t: by_table[t]["n_records"] for t in tables},
            "n_items_judged": len(ann["verdicts"]),
            "n_items_missed_added": sum(c["fn"] for per_record in ann["counts"].values() for c in per_record.values()),
            "unjudged_items": ann["unjudged"],
            "scores": scores,
            "by_table": by_table,
        }

        print(f"\n■ {name} — 표본: 사료 {len(ann['records'])}건 ("
              + ", ".join(f"{t} {by_table[t]['n_records']}" for t in tables)
              + f"), 판정 항목 {len(ann['verdicts'])}개, 미판정 {ann['unjudged']}개")
        print(f"  {'type':<22} {'P':>7} {'R':>7} {'F1':>7}   TP/FP/FN")
        for type_name, s in scores.items():
            print(f"  {type_name:<22} {fmt(s['precision'])} {fmt(s['recall'])} {fmt(s['f1'])}   {s['tp']}/{s['fp']}/{s['fn']}")
        print(f"  사료 유형(table)별 — {'table':<18} {'n':>4}  {'Entity P':>8} {'Entity R':>8}  {'Rel. P':>7} {'Rel. R':>7}")
        for table in tables:
            e, r = by_table[table]["scores"]["Entity (all)"], by_table[table]["scores"]["Relation (all)"]
            print(f"                      {table:<18} {by_table[table]['n_records']:>4}  "
                  f"{fmt(e['precision']):>8} {fmt(e['recall']):>8}  {fmt(r['precision']):>7} {fmt(r['recall']):>7}")
        if ann["unjudged"]:
            print(f"  ⚠️ verdict가 비어 있는 {ann['unjudged']}개 항목은 계산에서 제외했습니다.")

    names = list(annotations)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = annotations[names[i]]["verdicts"], annotations[names[j]]["verdicts"]
            shared = sorted(set(a) & set(b))
            kappa = cohen_kappa([a[k] for k in shared], [b[k] for k in shared])
            ac1 = gwet_ac1([a[k] for k in shared], [b[k] for k in shared])
            raw = sum(a[k] == b[k] for k in shared) / len(shared) if shared else float("nan")
            share_ok = sum((a[k] == VERDICT_OK) + (b[k] == VERDICT_OK) for k in shared) / (2 * len(shared)) if shared else float("nan")
            report["agreement"][f"{names[i]} vs {names[j]}"] = {
                "n_items": len(shared), "raw_agreement": None if raw != raw else round(raw, 4),
                "cohen_kappa": None if kappa != kappa else round(kappa, 4),
                "gwet_ac1": None if ac1 != ac1 else round(ac1, 4),
                "share_judged_correct": None if share_ok != share_ok else round(share_ok, 4),
            }
            print(f"\n🤝 {names[i]} vs {names[j]}: 공통 {len(shared)}개, 단순 일치율 {raw:.3f}, Cohen's κ {kappa:.3f}, "
                  f"Gwet's AC1 {ac1:.3f} (O 비율 {share_ok:.2f})")
            print("   (O가 대부분이면 κ는 일치율이 높아도 낮게 나옵니다. AC1과 일치율을 함께 보고하세요.)")
            print("   (missed 시트는 자유 기입이라 κ에 포함되지 않습니다. 재현율 차이로 확인하세요.)")

    write_json(output, report)
    print(f"\n💾 {output}")
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# Blind rating of answers
# ═════════════════════════════════════════════════════════════════════════════

SCALE = ["1", "2", "3", "4", "5"]
NA = "해당없음"
YES, NO = "Y", "N"
# Columns the rater fills in, with the values offered in the dropdown (None = free integer).
RATING_CHOICES: dict[str, list[str] | None] = {
    "declined": [YES, NO],
    "decline_justified": [YES, NO, NA],
    "accuracy": [*SCALE, NA],
    "completeness": SCALE,
    "unsupported_claims": None,
    "usefulness": SCALE,
    "narrative_order": [*SCALE, NA],
    "narrative_attribution": [*SCALE, NA],
}
RATING_COLUMNS = list(RATING_CHOICES)
SCALE_METRICS = ["accuracy", "completeness", "usefulness", "narrative_order", "narrative_attribution"]          # 1-5, higher is better
NUMERIC_METRICS = [*SCALE_METRICS, "unsupported_claims", "declined"]  # compared between systems
LENGTH_BIAS_METRICS = ["accuracy", "completeness"]
SUPPORT_VALUES = {"O": 1.0, "△": 0.5, "X": 0.0}
RATED_SYSTEMS = ("passage", "hybrid", "graph", "dcent")  # the systems compared for RQ1; ablations are not rated
FOCAL_SYSTEM = "dcent"            # planned contrasts: the focal system against each baseline (as in the paper)
CITES_PER_ANSWER = 3              # citations judged per answer, sampled with the seed
CITE_LABEL = "근거"               # record citations are shown as "[근거 n]" so the cited table does not reveal the system

RATING_INSTRUCTIONS = [
    "최종 답변 전문가 평가 시트 (블라인드)",
    "",
    "[answers] 시트: 같은 질문에 대한 여러 시스템의 답변이 무작위 순서로 섞여 있습니다. 어느 시스템인지는 표시하지 않습니다.",
    "",
    "※ 답변의 길이로 판단하지 마십시오. 긴 답변이 더 정확하거나 더 완전한 것은 아닙니다.",
    "   짧아도 질문에 필요한 내용을 틀림없이 담았으면 높은 점수를, 길어도 틀린 내용이나 근거 없는 서술이 있으면 그만큼 낮은 점수를 주십시오.",
    "   소제목·서론·결론 같은 형식이나 문장의 유려함도 점수에 반영하지 않습니다.",
    "",
    "1) declined (Y/N): 답변이 질문에 답하기를 거절(유보)했는가.",
    "   예) Y: \"자료에서 확인되지 않음\", \"제공된 사료만으로는 답할 수 없습니다\"가 답변의 결론인 경우.",
    "   예) N: 일부 내용에 '확인되지 않는다'는 단서를 달았더라도 질문에 대한 답을 실제로 제시한 경우.",
    "2) decline_justified (Y/N/해당없음): 거절이 타당한가. declined=N이면 '해당없음'.",
    "   예) Y: 이동휘가 아우내 장터 시위를 지휘했는지 묻는 질문처럼 전제가 사실이 아니어서, 확인되지 않는다고 답한 경우.",
    "   예) N: 사료에 답이 분명히 있는 질문(예: 특정 시위의 날짜와 주도 인물)인데도 확인되지 않는다고 답한 경우.",
    "3) accuracy (1~5): 답변에 틀린 내용이 있는가. 1=핵심 내용이 틀림, 3=핵심은 맞으나 세부 오류 있음, 5=틀린 내용 없음.",
    "   답변 전체가 거절뿐이면 '해당없음'을 고릅니다 (거절의 타당성은 2번에서 평가).",
    "   예) 5: 서술한 인물·날짜·장소가 모두 맞음. 내용이 적더라도 틀린 것이 없으면 5.",
    "   예) 2: 시위 날짜는 맞지만 주도 인물을 다른 지역 사건의 인물로 잘못 서술함.",
    "4) completeness (1~5): 질문에 답하는 데 필요한 내용을 담았는가. 1=필요한 내용이 거의 없음, 3=절반 정도, 5=빠진 것 없음.",
    "   예) 5: '누가, 언제, 어디서'를 물은 질문에 세 가지를 모두 답함 (두세 문장이어도 5).",
    "   예) 2: 긴 배경 설명은 있으나 질문이 물은 인물의 역할은 언급하지 않음.",
    "   타당한 거절(decline_justified=Y)은 거절 이유를 밝혔으면 5, 부당한 거절은 1로 채점합니다.",
    "5) unsupported_claims (0 이상의 정수): 사료로 뒷받침되지 않는 주장의 개수. 인용 표시가 붙어 있는지와 무관하게 셉니다.",
    "   예) 인용 없이 \"이 시위에는 약 3,000명이 참여했다\"고 썼고 사료에서 확인되지 않으면 1개.",
    "   예) [근거 1]을 인용했지만 그 사료에 해당 내용이 없으면 이것도 1개. 사료로 확인되는 주장은 인용이 없어도 세지 않습니다.",
    "6) usefulness (1~5): 질문한 연구자·관람객에게 실제로 도움이 되는가. 1=쓸모없음, 5=그대로 활용 가능.",
    "   예) 5: 답과 근거 사료를 바로 확인해 인용할 수 있음.",
    "   예) 2: 틀린 내용은 없으나 질문과 관련이 적은 일반적 서술이 대부분임.",
    "7) narrative_order (1~5/해당없음): 여러 사건의 경과·확산을 묻는 질문에서 사건이 올바른 시간 순서로 배열되었는가.",
    "   1=순서가 대부분 틀림, 3=일부 뒤바뀜, 5=모두 올바름. 한 사건만 묻는 질문이거나 거절한 답변이면 '해당없음'.",
    "   예) 2: 3월 26일 시위를 3월 1일 서울 시위보다 앞선 사건으로 서술함.",
    "8) narrative_attribution (1~5/해당없음): 서사에 등장하는 인물·장소·행위가 올바른 사건과 사료에 귀속되었는가.",
    "   1=다른 사건·지역의 인물이나 행위가 섞임, 3=일부 잘못 귀속, 5=모두 올바름. 7번과 같은 경우 '해당없음'.",
    "   예) 2: 이웃 면의 시위 주도자를 질문한 시위의 주도자로 서술함.",
    "",
    "답변 속 기록 인용은 시스템을 알아볼 수 없도록 [근거 1], [근거 2]처럼 번호로 바꾸어 표시했습니다.",
    "",
    "[citations] 시트: 답변 문장(claim)과 그 문장이 인용한 사료 원문(source_text)입니다. 답변마다 인용을 최대 3건까지 뽑아 제시합니다.",
    "  source_id는 삼일운동 데이터베이스의 식별자로, 원문을 데이터베이스에서 직접 확인할 때 씁니다.",
    "  - supports: O=사료가 문장을 뒷받침함, △=일부만 뒷받침함, X=뒷받침하지 않거나 무관함.",
    "다른 평가자와 상의하지 말고 독립적으로 채점해 주십시오. item_id와 cite_id는 수정하지 마십시오.",
]


def _is_failed(record: dict[str, Any]) -> bool:
    answer = str(record.get("response", "")).strip()
    return bool(record.get("generation_failed")) or not answer or bool(re.match(r"\[[A-Za-z ]*Error", answer))


RECORD_TABLES = {"bibliography", "source_info", "event_info", "detail_place"}


def _source_texts(keys: set[str]) -> dict[str, tuple[str, str]]:
    """Record text and source-database identifier for citation keys "table:rowid" (see metrics.record_key)."""
    by_table: dict[str, list[int]] = defaultdict(list)
    for key in keys:
        table, rowid = key.split(":")
        if table in RECORD_TABLES:
            by_table[table].append(int(rowid))
    if not by_table:
        return {}
    conn = get_pg_connection()
    cur = conn.cursor()
    out = {}
    for table, rowids in by_table.items():
        id_col = SOURCE_ID_COLUMNS.get(f"raw_{table}")
        id_expr = f'"{id_col}"' if id_col else "NULL"
        cur.execute(f'SELECT rowid, tei, {id_expr} FROM "raw_{table}" WHERE rowid = ANY(%s)', (sorted(rowids),))
        for rowid, tei, source_id in cur.fetchall():
            out[f"{table}:{int(rowid)}"] = (_body_text(tei or ""), str(source_id or "").strip())
    cur.close()
    conn.close()
    return out


def _neutral_citations(text: str) -> tuple[str, dict[int, str]]:
    """Replace record citations with "[근거 n]", numbered per answer; return the text and n -> record key."""
    numbers: dict[str, int] = {}

    def label(m: re.Match) -> str:
        key = record_key(m.group(1), m.group(2))
        numbers.setdefault(key, len(numbers) + 1)
        return f"[{CITE_LABEL} {numbers[key]}]"

    return ID_RE.sub(label, text), {n: k for k, n in numbers.items()}


def rate_export(run_dir: Path, n_questions: int = 0, seed: int = 42, raters: tuple[str, ...] = ("A", "B")) -> int:
    """Blind rating workbooks for a run's answers (systems in RATED_SYSTEMS only)."""
    out_dir, key_dir = RATING_DIR, RATING_KEY_DIR
    _refuse_overwrite([out_dir / f"rating_{r}.xlsx" for r in raters] + [key_dir / "blinding_key.json"])
    records = [r for r in read_json(run_dir / "answers.json") if r.get("system") in RATED_SYSTEMS]
    if not records:
        raise SystemExit(f"평가할 답변이 없습니다 ({', '.join(RATED_SYSTEMS)} 시스템의 답변이 {run_dir}에 없음).")
    n_models = len({r["model"] for r in records})
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_question[record["query_id"]].append(record)

    usable = {q: rs for q, rs in by_question.items() if len(rs) == n_models and not any(_is_failed(r) for r in rs)}
    skipped = len(by_question) - len(usable)

    rng = random.Random(seed)
    question_ids = sorted(usable)
    if n_questions and n_questions < len(question_ids):
        # Sample evenly across categories so every category is rated.
        by_category: dict[str, list[str]] = defaultdict(list)
        for q in question_ids:
            by_category[usable[q][0].get("category", "")].append(q)
        for ids in by_category.values():
            rng.shuffle(ids)
        picked = [q for group in itertools.zip_longest(*by_category.values()) for q in group if q]
        question_ids = sorted(picked[:n_questions])

    answer_rows, citation_rows, key = [], [], {}
    for query_id in question_ids:
        answers = list(usable[query_id])
        rng.shuffle(answers)
        for label, record in zip("ABCDEFGH", answers):
            item_id = f"{query_id}-{label}"
            shown, cited_keys = _neutral_citations(record["response"])
            found = []    # (claim, citation number), in order, one per sentence and record
            for sentence in re.split(r"(?<=[.!?。])\s+|\n+", shown):
                for n in dict.fromkeys(int(m.group(1)) for m in re.finditer(rf"\[{CITE_LABEL} (\d+)\]", sentence)):
                    found.append((sentence.strip(), n))
            picked = sorted(rng.sample(range(len(found)), min(CITES_PER_ANSWER, len(found))))
            key[item_id] = {"run": run_dir.name, "query_id": query_id, "system": record.get("system", ""), "model": record["model"],
                            "category": record.get("category", ""), "source": record.get("source", ""),
                            "answer_chars": len(record["response"]), "citations": cited_keys, "n_citations": len(found)}
            answer_rows.append({"item_id": item_id, "query_id": query_id, "question": record["user_input"],
                                "answer_label": f"답변 {label}", "answer": shown})
            for i in picked:
                claim, n = found[i]
                citation_rows.append({"cite_id": f"{item_id}#{len(citation_rows) + 1}", "item_id": item_id,
                                      "claim": claim, "cited_source": f"[{CITE_LABEL} {n}]", "_key": cited_keys[n]})

    sources = _source_texts({row["_key"] for row in citation_rows})
    for row in citation_rows:
        text, source_id = sources.get(row.pop("_key"), ("[해당 사료 없음: 존재하지 않는 ID 인용]", ""))
        row["source_id"], row["source_text"] = source_id, text

    out_dir.mkdir(parents=True, exist_ok=True)
    for rater in raters:
        wb = new_workbook(RATING_INSTRUCTIONS)
        add_sheet(wb, "answers", ["item_id", "query_id", "question", "answer_label", "answer", *RATING_COLUMNS, "note"], answer_rows,
                  widths={"item_id": 14, "question": 50, "answer": 100, "note": 30},
                  choices={c: values for c, values in RATING_CHOICES.items() if values})
        add_sheet(wb, "citations", ["cite_id", "item_id", "claim", "cited_source", "source_id", "source_text", "supports", "note"], citation_rows,
                  widths={"cite_id": 16, "item_id": 14, "claim": 60, "source_id": 18, "source_text": 100, "note": 30},
                  choices={"supports": list(SUPPORT_VALUES)})
        path = out_dir / f"rating_{rater}.xlsx"
        wb.save(path)
        print(f"📝 {path}")
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / "blinding_key.json"
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"🔑 {key_path}  (평가자에게 전달하지 마세요. 평가자에게는 {out_dir} 폴더만 전달합니다)")
    total_cites = sum(v["n_citations"] for v in key.values())
    print(f"✅ 질문 {len(question_ids)}건 × 모델 {n_models}개 = 답변 {len(answer_rows)}개, "
          f"인용 검증 {len(citation_rows)}건 (전체 인용 {total_cites}건 중 답변당 최대 {CITES_PER_ANSWER}건)")
    if skipped:
        print(f"⚠️ 생성 실패 답변이 있어 제외한 질문: {skipped}건")
    cited_models = {key[row["item_id"]]["model"] for row in citation_rows}
    uncited = sorted({v["model"] for v in key.values()} - cited_models)
    if uncited:
        print(f"ℹ️ 사료 ID를 인용하지 않는 모델(인용 정확성 산출 불가): {uncited}")
    return 0


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _fmt(value: float) -> str:
    return "  n/a" if value != value else f"{value:5.2f}"


def rate_score(files: list[Path]) -> int:
    """Score completed rating workbooks (default: every rating_*.xlsx in annotation/rating/)."""
    files = files or sorted(RATING_DIR.glob("rating_*.xlsx"))
    key_path = RATING_KEY_DIR / "blinding_key.json"
    if not files or not key_path.exists():
        raise SystemExit(f"채점할 워크북 또는 블라인드 키가 없습니다: {RATING_DIR}, {key_path}")
    key = read_json(key_path)
    run_names = {v.get("run") for v in key.values()}
    from evaluation.common import RESULTS_DIR
    output = (RESULTS_DIR / run_names.pop() / "rating.json") if len(run_names) == 1 and None not in run_names \
        else RESULTS_DIR / "rating.json"
    raters = {path.stem: path for path in files}

    # ratings[metric][rater][item_id]
    ratings: dict[str, dict[str, dict[str, float]]] = {m: defaultdict(dict) for m in RATING_COLUMNS}
    supports: dict[str, dict[str, str]] = defaultdict(dict)
    cite_item: dict[str, str] = {}
    answer_chars: dict[str, int] = {item_id: v["answer_chars"] for item_id, v in key.items() if "answer_chars" in v}
    invalid_cells = 0
    for rater, path in raters.items():
        for row in read_sheet(path, "answers"):
            answer_chars.setdefault(row["item_id"], len(row.get("answer", "")))
            for metric in RATING_COLUMNS:
                cell = row.get(metric, "").upper()
                if metric in ("declined", "decline_justified"):
                    value = {YES: 1.0, NO: 0.0}.get(cell)
                else:
                    low, high = (0, 10**6) if metric == "unsupported_claims" else (1, 5)
                    value = float(cell) if re.fullmatch(r"\d+(\.0+)?", cell) and low <= float(cell) <= high else None
                if value is not None:
                    ratings[metric][rater][row["item_id"]] = value
                elif cell and cell != NA:
                    invalid_cells += 1
        for row in read_sheet(path, "citations"):
            cite_item[row["cite_id"]] = row["item_id"]
            if row["supports"] in SUPPORT_VALUES:
                supports[rater][row["cite_id"]] = row["supports"]

    models = list(dict.fromkeys(v["model"] for v in key.values()))
    report: dict[str, Any] = {"n_raters": len(raters), "n_items": len(key), "n_questions": len({v["query_id"] for v in key.values()}),
                              "models": {}, "significance": [], "agreement": {}, "length_bias": {}}

    # Per-question score of a model = mean over raters.
    per_question: dict[str, dict[str, dict[str, float]]] = {m: defaultdict(dict) for m in RATING_COLUMNS}
    for metric in RATING_COLUMNS:
        collected: dict[tuple[str, str], list[float]] = defaultdict(list)
        for rater_scores in ratings[metric].values():
            for item_id, score in rater_scores.items():
                collected[(key[item_id]["model"], key[item_id]["query_id"])].append(score)
        for (model, query_id), values in collected.items():
            per_question[metric][model][query_id] = sum(values) / len(values)

    print(f"평가자 {len(raters)}명, 질문 {report['n_questions']}건, 답변 {len(key)}개")
    print("declined·decline_justified는 비율(0~1), unsupported_claims는 답변당 개수, 나머지는 1~5점 평균입니다.")
    print(f"{'model':<42} {'metric':<18} {'n':>4} {'mean':>6}  95% CI")
    for model in models:
        report["models"][model] = {}
        for metric in RATING_COLUMNS:
            values = list(per_question[metric][model].values())
            mean, lo, hi = bootstrap_ci(values)
            report["models"][model][metric] = {"n": len(values), "mean": mean, "ci95": [lo, hi]}
            print(f"{model:<42} {metric:<18} {len(values):>4} {_fmt(mean)}  [{_fmt(lo)}, {_fmt(hi)}]")

        strict, lenient = [], []
        for rater_supports in supports.values():
            for cite_id, value in rater_supports.items():
                if key[cite_item[cite_id]]["model"] == model:
                    strict.append(1.0 if value == "O" else 0.0)
                    lenient.append(SUPPORT_VALUES[value])
        report["models"][model]["citation_accuracy"] = {
            "n_judgements": len(strict),
            "strict": sum(strict) / len(strict) if strict else None,
            "lenient": sum(lenient) / len(lenient) if lenient else None,
        }
        if strict:
            print(f"{model:<42} {'citation_accuracy':<18} {len(strict):>4} {_fmt(sum(strict) / len(strict))}  (△ 0.5점 포함 시 {_fmt(sum(lenient) / len(lenient))})")
        else:
            print(f"{model:<42} {'citation_accuracy':<18} {0:>4}   n/a  (인용 없음)")

    system_of = {v["model"]: v.get("system", "") for v in key.values()}
    focal = next((m for m in models if system_of.get(m) == FOCAL_SYSTEM), None)
    pairs = [(focal, m) for m in models if m != focal] if focal else list(itertools.combinations(models, 2))
    print("\n계획된 비교: " + (f"{focal} 대 각 기준선" if focal else "모든 쌍") + " (sign-flip permutation, 지표별 Holm 보정)")
    report["contrasts"] = "focal_vs_baselines" if focal else "all_pairs"
    for metric in NUMERIC_METRICS:
        rows = []
        for model_a, model_b in pairs:
            shared = sorted(set(per_question[metric][model_a]) & set(per_question[metric][model_b]))
            a = [per_question[metric][model_a][q] for q in shared]
            b = [per_question[metric][model_b][q] for q in shared]
            diff, lo, hi = bootstrap_ci([x - y for x, y in zip(a, b)])
            rows.append({"metric": metric, "model_a": model_a, "model_b": model_b, "n_pairs": len(shared),
                         "mean_diff_a_minus_b": diff, "ci95": [lo, hi], "p_permutation": paired_permutation_test(a, b)})
        for row, adjusted in zip(rows, holm_adjust([r["p_permutation"] for r in rows])):
            row["p_holm"] = adjusted
            print(f"  {metric:<18} {row['model_a']} vs {row['model_b']}: Δ={_fmt(row['mean_diff_a_minus_b'])} (n={row['n_pairs']}), p_holm={adjusted:.4f}")
        report["significance"].extend(rows)

    for rater_a, rater_b in itertools.combinations(raters, 2):
        entry: dict[str, Any] = {}
        for metric in RATING_COLUMNS:
            shared = sorted(set(ratings[metric][rater_a]) & set(ratings[metric][rater_b]))
            # Ordinal scales and counts: quadratic-weighted kappa. Y/N columns: plain kappa.
            weights = None if metric in ("declined", "decline_justified") else "quadratic"
            entry[metric] = {"n": len(shared), "weighted_kappa" if weights else "kappa": cohen_kappa(
                [int(ratings[metric][rater_a][i]) for i in shared], [int(ratings[metric][rater_b][i]) for i in shared], weights=weights)}
        shared = sorted(set(supports[rater_a]) & set(supports[rater_b]))
        entry["citation_supports"] = {"n": len(shared), "kappa": cohen_kappa(
            [supports[rater_a][i] for i in shared], [supports[rater_b][i] for i in shared])}
        report["agreement"][f"{rater_a} vs {rater_b}"] = entry
        print(f"\n🤝 {rater_a} vs {rater_b}: " + ", ".join(
            f"{name} κ={_fmt(v.get('weighted_kappa', v.get('kappa')))} (n={v['n']})" for name, v in entry.items()))

    # Length bias: do longer answers get higher ratings? (item score = mean over raters)
    print("\n📏 길이 편향 점검: 답변 길이(글자 수)와 점수의 상관계수")
    for metric in LENGTH_BIAS_METRICS:
        by_item: dict[str, list[float]] = defaultdict(list)
        for rater_scores in ratings[metric].values():
            for item_id, score in rater_scores.items():
                by_item[item_id].append(score)
        items = sorted(i for i in by_item if i in answer_chars)
        lengths = [float(answer_chars[i]) for i in items]
        scores = [sum(by_item[i]) / len(by_item[i]) for i in items]
        pearson = _pearson(lengths, scores)
        # Within systems: subtract each system's mean length and score, so that a better system that also
        # writes longer answers does not appear as a length bias.
        by_model: dict[str, list[int]] = defaultdict(list)
        for n, i in enumerate(items):
            by_model[key[i]["model"]].append(n)
        res_len, res_score, per_model = [], [], {}
        for model, idx in by_model.items():
            ml = sum(lengths[n] for n in idx) / len(idx)
            ms = sum(scores[n] for n in idx) / len(idx)
            res_len += [lengths[n] - ml for n in idx]
            res_score += [scores[n] - ms for n in idx]
            per_model[model] = _pearson([lengths[n] for n in idx], [scores[n] for n in idx])
        within = _pearson(res_len, res_score)
        report["length_bias"][metric] = {"n": len(items), "pearson_r": pearson, "within_system_r": within,
                                         "by_system": per_model}
        print(f"  {metric:<14} 전체 r={_fmt(pearson)}, 시스템 내 r={_fmt(within)} (n={len(items)})")
    print("  (시스템 내 r이 길이 편향의 지표입니다. 전체 r은 시스템 간 품질 차이와 섞입니다.)")
    if invalid_cells:
        print(f"\n⚠️ 허용되지 않는 값이 들어 있어 무시한 칸: {invalid_cells}개")
        report["invalid_cells"] = invalid_cells

    write_json(output, report)  # NaN is stored as null
    print(f"\n💾 {output}")
    return 0
