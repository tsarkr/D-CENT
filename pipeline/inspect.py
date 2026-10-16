"""Read-only diagnostics of the databases behind the web application.

  python -m pipeline inspect names "질문"      namesIndex full-text lookup (the graph action's cross-check)
  python -m pipeline inspect family 유관순     kinship edges of a person and kinship words near the name in the records
  python -m pipeline inspect search 이름 ...   time of the record search (pg_prefetch) per table, index use
  python -m pipeline inspect counts            nodes per label, relationships per type -> evaluation/results/graph/
  python -m pipeline inspect sparsity          how sparse the Event layer is, and why -> evaluation/results/graph/
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from typing import Any

from pipeline.config import GRAPH_REPORT_DIR, get_neo4j_driver, get_pg_connection

DEFAULT_QUESTION = "보성사에서 손병희를 명예총재로, 이종일을 단장으로 결성된 천도교 항일비밀결사는?"
DEFAULT_NAMES = ["유관순", "아우내", "아우내 장터", "만세운동", "柳寬順"]


# ── names: namesIndex full-text lookup ────────────────────────────────────────
def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def display(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (list, tuple)):
        return str(list(value))
    return str(value)


def fulltext_results(session: Any, query: str) -> list[dict[str, Any]]:
    """Run the same namesIndex query used by docent.php's graph action."""
    section("PHP namesIndex 검색 경로 Top-5")
    metadata_query = """
    SHOW INDEXES
    YIELD name, type, state, labelsOrTypes, properties
    WHERE name = 'namesIndex'
    RETURN name, type, state, labelsOrTypes, properties
    """
    try:
        metadata = session.run(metadata_query).single()
    except Exception as exc:
        metadata = None
        print(f"namesIndex 메타데이터 조회 실패: {exc}")
    if metadata:
        metadata_row = metadata.data()
        labels = metadata_row.get("labelsOrTypes") or []
        print(
            f"index=name={display(metadata_row.get('name'))}, "
            f"type={display(metadata_row.get('type'))}, "
            f"state={display(metadata_row.get('state'))}, "
            f"labelsOrTypes={display(labels)}, "
            f"properties={display(metadata_row.get('properties'))}"
        )
        if "Thesaurus" not in labels:
            print("[원인 후보] namesIndex 대상 라벨에 Thesaurus가 없습니다.")
    else:
        print("namesIndex 메타데이터를 찾지 못했습니다.")
    cypher = """
    CALL db.index.fulltext.queryNodes('namesIndex', $term)
    YIELD node, score
    RETURN node AS n, labels(node) AS labels, score
    ORDER BY score DESC
    LIMIT 5
    """
    try:
        records = list(session.run(cypher, term=query))
    except Exception as exc:
        print(f"namesIndex 검색 실패: {exc}")
        print("PHP도 같은 namesIndex 호출 후 fallback 검색으로 전환할 가능성이 있습니다.")
        return []
    if not records:
        print("검색 결과가 없습니다.")
        return []
    results = []
    for rank, record in enumerate(records, start=1):
        node = record.get("n")
        labels = list(record.get("labels") or [])
        properties = dict(node.items()) if node is not None else {}
        result = {
            "rank": rank,
            "labels": labels,
            "name": properties.get("name") or properties.get("명칭") or properties.get("title"),
            "score": record.get("score"),
        }
        results.append(result)
        print(
            f"{rank}. labels={labels}, name={display(result['name'])}, "
            f"score={display(result['score'])}"
        )
    if not any("Thesaurus" in result["labels"] for result in results):
        print("[주의] PHP namesIndex Top-5에 Thesaurus가 없습니다.")
    return results


def names(question: str) -> int:
    driver = get_neo4j_driver(notifications_min_severity="OFF")
    try:
        with driver.session() as session:
            fulltext_results(session, question)
    finally:
        driver.close()
    return 0


# ── family: kinship data of one person ────────────────────────────────────────
def family(name: str) -> int:
    driver = get_neo4j_driver(notifications_min_severity="OFF")
    with driver.session() as s:
        print("== 그래프의 가족·지인 관계 전체")
        for r in s.run("MATCH (a)-[r:P152_has_parent|`foaf:knows`]->(b) "
                       "RETURN type(r) AS t, a.명칭 AS a, b.명칭 AS b, r.label AS label"):
            print(f"  {r['a']} -[{r['t']}]-> {r['b']}  {r['label'] or ''}")
        print(f"\n== '{name}' 인물 노드와 연결")
        for r in s.run("MATCH (p:Person) WHERE p.명칭 = $n OR p.한글명칭 = $n OR p.한글독음 = $n "
                       "OPTIONAL MATCH (p)-[r]-(q) WITH p, type(r) AS t, count(*) AS c "
                       "RETURN p.명칭 AS name, p.한글독음 AS reading, collect(t + ':' + toString(c)) AS rels", n=name):
            print(f"  {r['name']} ({r['reading']}): {r['rels']}")
    driver.close()

    conn = get_pg_connection()
    cur = conn.cursor()
    print(f"\n== 기록 본문에서 '{name}' 주변의 가족 표현")
    for table, col in (("raw_bibliography", "문서요약"), ("raw_source_info", "출처정보")):
        cur.execute(f'SELECT count(*) FROM "{table}" WHERE "{col}" LIKE %s', (f"%{name}%",))
        total = cur.fetchone()[0]
        cur.execute(f'SELECT substring("{col}" from position(%s in "{col}") for 80) FROM "{table}" '
                    f'WHERE "{col}" LIKE %s AND "{col}" ~ %s LIMIT 5',
                    (name, f"%{name}%", "(부친|아버지|모친|어머니|아들|딸|숙부|오빠|동생|형)"))
        rows = cur.fetchall()
        print(f"  {table}.{col}: 이름 포함 {total}건, 가족어 동반 예시 {len(rows)}건")
        for (snip,) in rows:
            print("   -", (snip or "").replace("\n", " "))
    conn.close()
    return 0


# ── search: record search timing ──────────────────────────────────────────────
def search(names: list[str]) -> int:
    conn = get_pg_connection()
    cur = conn.cursor()
    cur.execute("SELECT indexrelname FROM pg_stat_user_indexes WHERE indexrelname LIKE '%trgm' ORDER BY 1")
    print("trigram indexes:", [r[0] for r in cur.fetchall()] or "NONE")

    cur.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='tei' ORDER BY 1")
    tables = [r[0] for r in cur.fetchall()]
    RECORD_TABLES = {"raw_bibliography", "raw_source_info", "raw_event_info", "raw_detail_place"}
    tables = [t for t in tables if t in RECORD_TABLES]
    long_names = [n for n in names if len(n) >= 3]
    total = 0.0
    for t in tables:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position LIMIT 1", (t,))
        id_col = cur.fetchone()[0]
        params = list(names)
        branches = [f'(SELECT * FROM "{t}" WHERE "{id_col}"::text IN ({",".join(["%s"]*len(names))}) LIMIT 25)']
        if long_names:
            branches.append(f'(SELECT * FROM "{t}" WHERE ' + " OR ".join(["tei ILIKE %s"] * len(long_names)) + " LIMIT 25)")
            params += [f"%{n}%" for n in long_names]
        sql = " UNION ALL ".join(branches)
        cur.execute("EXPLAIN " + sql, params)
        plan = " ".join(r[0] for r in cur.fetchall())
        uses_index = "trgm" in plan
        t0 = time.time()
        cur.execute(sql, params)
        rows = cur.fetchall()
        dt = time.time() - t0
        total += dt
        size = sum(len(str(r)) for r in rows)
        print(f"{t:<32} {dt:6.2f}s  rows={len(rows):<3} size={size/1024:7.1f}KB  index={'yes' if uses_index else 'NO'}")
    print(f"total {total:.2f}s")
    conn.close()
    return 0


# ── counts: graph size tables ─────────────────────────────────────────────────
def counts() -> int:
    driver = get_neo4j_driver(notifications_min_severity="OFF")
    with driver.session() as session:
        labels = [r["label"] for r in session.run("CALL db.labels() YIELD label RETURN label")]
        nodes = []
        for label in labels:
            n = session.run(f"MATCH (n:`{label}`) RETURN count(n) AS n").single()["n"]
            nodes.append((label, n))
        nodes.sort(key=lambda x: -x[1])
        total_nodes = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        rels = [(r["type"], r["n"]) for r in session.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n ORDER BY n DESC")]
        total_rels = sum(n for _, n in rels)
    driver.close()

    GRAPH_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows, total in (("node_counts.csv", nodes, total_nodes),
                              ("relationship_counts.csv", rels, total_rels)):
        with (GRAPH_REPORT_DIR / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label" if "node" in name else "type", "count"])
            w.writerows(rows)
            w.writerow(["TOTAL", total])
        print(f"{GRAPH_REPORT_DIR / name}")
    print(f"nodes {total_nodes:,}, relationships {total_rels:,}")
    return 0


# ── sparsity: Event layer ─────────────────────────────────────────────────────
# Event-labelled nodes that are not events: no 사건 id, minted from a CIDOC ex: resource.
ARTIFACT_WHERE = "e.id IS NULL AND e.uid STARTS WITH 'ex:'"


def build_report(session) -> dict:
    def rows(cypher: str) -> list[dict]:
        return [r.data() for r in session.run(cypher)]

    total = rows("MATCH (e:Event) RETURN count(e) AS n")[0]["n"]
    isolated = rows("MATCH (e:Event) WHERE NOT (e)--() RETURN count(e) AS n")[0]["n"]

    origins = rows(f"""
        MATCH (e:Event)
        WITH e, CASE
            WHEN e.id IS NOT NULL THEN 'event (raw_event_info 사건 1건)'
            WHEN e.uid STARTS WITH 'ex:activity_' THEN 'artifact: CIDOC E7_Activity (인명 태그 1개당 1노드)'
            WHEN e.uid STARTS WITH 'ex:actor_' THEN 'artifact: CIDOC E39_Actor (인물인데 Event로 분류)'
            WHEN {ARTIFACT_WHERE} THEN 'artifact: CIDOC 행 단위 래퍼 (ex:<table>_<rowid>)'
            ELSE 'other' END AS origin
        RETURN origin, count(e) AS nodes, sum(CASE WHEN NOT (e)--() THEN 1 ELSE 0 END) AS isolated
        ORDER BY nodes DESC
    """)

    degrees = [r["deg"] for r in rows("MATCH (e:Event) WHERE e.id IS NOT NULL RETURN COUNT {(e)--()} AS deg")]
    real = {"nodes": len(degrees)}
    if degrees:
        ordered = sorted(degrees)
        real.update({
            "isolated": sum(1 for d in degrees if d == 0),
            "degree_min": ordered[0],
            "degree_median": statistics.median(ordered),
            "degree_mean": round(statistics.mean(ordered), 2),
            "degree_p90": ordered[int(0.9 * (len(ordered) - 1))],
            "degree_max": ordered[-1],
            "without_person": rows("MATCH (e:Event) WHERE e.id IS NOT NULL AND NOT (e)-[:P14_carried_out_by]->(:Person) RETURN count(e) AS n")[0]["n"],
            "without_place": rows("MATCH (e:Event) WHERE e.id IS NOT NULL AND NOT (e)-[:P7_took_place_at]->(:Place) RETURN count(e) AS n")[0]["n"],
        })
        real["relations"] = rows("MATCH (e:Event)-[r]-() WHERE e.id IS NOT NULL RETURN type(r) AS type, count(r) AS n ORDER BY n DESC")

    by_label = rows("""
        MATCH (n)
        WITH coalesce(labels(n)[0], '(no label)') AS label, COUNT {(n)--()} AS deg
        RETURN label, count(*) AS nodes, sum(CASE WHEN deg = 0 THEN 1 ELSE 0 END) AS isolated
        ORDER BY nodes DESC
    """)

    conn = get_pg_connection()
    cur = conn.cursor()
    cur.execute('SELECT count(*), count(DISTINCT "아이디") FROM raw_event_info')
    pg_rows, pg_ids = cur.fetchone()
    cur.close()
    conn.close()

    return {
        "event_unit": "raw_event_info 1행 = 사건 1건 (Event.id = 사건 아이디)",
        "event_nodes_total": total,
        "event_nodes_isolated": isolated,
        "isolated_ratio": round(isolated / total, 4) if total else None,
        "by_origin": origins,
        "all_labels": by_label,
        "real_events": real,
        "source": {"raw_event_info_rows": pg_rows, "raw_event_info_distinct_ids": pg_ids},
    }


def print_report(report: dict) -> None:
    total = report["event_nodes_total"]
    print(f"Event 단위: {report['event_unit']}")
    print(f"Event 노드 {total:,}개 중 고립 {report['event_nodes_isolated']:,}개 ({(report['isolated_ratio'] or 0) * 100:.1f}%)\n")
    print(f"{'출처':<52} {'노드':>8} {'고립':>8}")
    for row in report["by_origin"]:
        print(f"{row['origin']:<52} {row['nodes']:>8,} {row['isolated']:>8,}")
    print(f"\n{'레이블':<20} {'노드':>8} {'고립':>8}")
    for row in report["all_labels"]:
        print(f"{row['label']:<20} {row['nodes']:>8,} {row['isolated']:>8,}")
    real = report["real_events"]
    if real.get("nodes"):
        print(f"\n실제 사건 {real['nodes']:,}개 (원본 raw_event_info {report['source']['raw_event_info_rows']:,}행, "
              f"고유 아이디 {report['source']['raw_event_info_distinct_ids']:,}개)")
        print(f"  고립 {real['isolated']:,}개, 인물 미연결 {real['without_person']:,}개, 장소 미연결 {real['without_place']:,}개")
        print(f"  차수: 최소 {real['degree_min']}, 중앙값 {real['degree_median']}, 평균 {real['degree_mean']}, "
              f"p90 {real['degree_p90']}, 최대 {real['degree_max']}")


def sparsity() -> int:
    driver = get_neo4j_driver(notifications_min_severity="OFF")
    try:
        with driver.session() as session:
            report = build_report(session)
    finally:
        driver.close()
    print_report(report)
    GRAPH_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = GRAPH_REPORT_DIR / "sparsity.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 {path}")
    return 0


def main(what: str | None, terms: list[str]) -> int:
    if what == "names":
        return names(" ".join(terms) or DEFAULT_QUESTION)
    if what == "family":
        return family(terms[0] if terms else "유관순")
    if what == "search":
        return search(terms or DEFAULT_NAMES)
    if what == "counts":
        return counts()
    if what == "sparsity":
        return sparsity()
    print(__doc__)
    return 0 if what is None else 2
