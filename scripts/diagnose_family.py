#!/usr/bin/env python3
"""Read-only check of kinship data for one person (default 유관순).

Prints the P152_has_parent / foaf:knows edges in the graph, the person's nodes
and their kinship edges, and how often kinship words occur near the name in the
record text, to tell whether missing kinship in answers is a data gap or a
retrieval problem.

Usage: .venv3.14/bin/python scripts/diagnose_family.py [이름]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.config import get_neo4j_driver, get_pg_connection

name = sys.argv[1] if len(sys.argv) > 1 else "유관순"
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
