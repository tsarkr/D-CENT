"""Step 5: the PostgreSQL indexes that the web application's searches rely on.

The ingest step drops and recreates the source tables, which also drops their
indexes. Without them, the ILIKE '%name%' searches of docent.php (record lookup
for a question and for a selected node) fall back to sequential scans over the
TEI of every table on the remote database. This step recreates:

  * a GIN trigram index on ``tei`` for every table that has a ``tei`` column;
  * GIN trigram indexes on the person columns searched directly
    (raw_event_info."관련인물", raw_source_info."관련인물");
  * a GIN trigram index on tei_cidoc_mappings.mapping_label;
  * a B-tree index on the first (id) column, cast to text, of the record tables.

Indexes are created CONCURRENTLY and IF NOT EXISTS, so the step is safe to rerun.
"""

from __future__ import annotations

import re


from pipeline.config import get_pg_connection, table_has_column

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def main() -> int:
    conn = get_pg_connection()
    conn.autocommit = True  # CREATE INDEX CONCURRENTLY cannot run inside a transaction
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    cur.execute("SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'tei' ORDER BY table_name")
    targets = [(t, "tei") for (t,) in cur.fetchall() if _IDENT.match(t)]
    for table, column in (("raw_event_info", "관련인물"), ("raw_source_info", "관련인물"),
                          ("tei_cidoc_mappings", "mapping_label")):
        if table_has_column(cur, table, column):
            targets.append((table, column))

    for table, column in targets:
        suffix = "tei" if column == "tei" else ("label" if column == "mapping_label" else "persons")
        index = f"{table}_{suffix}_trgm"
        print(f"  index {index} on {table}.{column}", flush=True)
        cur.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index}" '
                    f'ON "{table}" USING gin ("{column}" gin_trgm_ops)')
        cur.execute(f'ANALYZE "{table}"')
    # B-tree index on the first column cast to text: docent.php matches node names
    # against it with "col"::text IN (...), which a plain column index cannot serve.
    for table in ("raw_bibliography", "raw_source_info", "raw_event_info", "raw_detail_place"):
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = %s ORDER BY ordinal_position LIMIT 1", (table,))
        row = cur.fetchone()
        if not row:
            continue
        index = f"{table}_id_text"
        print(f"  index {index} on {table}.{row[0]}::text", flush=True)
        cur.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index}" ON "{table}" (("{row[0]}"::text))')
        # rowid: document nodes point to records by (table, rowid)
        if table_has_column(cur, table, "rowid"):
            print(f"  index {table}_rowid on {table}.rowid", flush=True)
            cur.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{table}_rowid" ON "{table}" (rowid)')
    cur.close()
    conn.close()
    print(f"Trigram indexes ensured: {len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
