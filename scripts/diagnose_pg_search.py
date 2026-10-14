#!/usr/bin/env python3
"""Time the record search of docent.php (pg_prefetch) per table, read-only.

Runs the same query shape as includes/database.php::fetch_pg_rows_for_names
(id IN (...) OR tei ILIKE '%name%' OR ...) for each table with a tei column,
prints the elapsed time, rows, transferred size, and whether the plan uses the
trigram index. Also lists existing *_trgm indexes.

Usage: .venv3.14/bin/python scripts/diagnose_pg_search.py [name ...]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.config import get_pg_connection

names = sys.argv[1:] or ["유관순", "아우내", "아우내 장터", "만세운동", "柳寬順"]
conn = get_pg_connection()
cur = conn.cursor()
cur.execute("SELECT indexrelname FROM pg_stat_user_indexes WHERE indexrelname LIKE '%trgm' ORDER BY 1")
print("trigram indexes:", [r[0] for r in cur.fetchall()] or "NONE")

cur.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='tei' ORDER BY 1")
tables = [r[0] for r in cur.fetchall()]
RECORD_TABLES = {"raw_bibliography", "raw_source_info", "raw_event_info", "raw_detail_place"}
show_all = "--all" in sys.argv
if "--all" in names: names.remove("--all")
if not show_all: tables = [t for t in tables if t in RECORD_TABLES]
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
