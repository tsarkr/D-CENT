"""Step 1: load the source files into PostgreSQL and wrap every row in TEI.

load_sources()  data/*.csv|xlsx -> raw_* tables (dropped and recreated)
wrap_tei()      each raw_* table gets rowid and a tei column (rule-based tagging)
"""

import csv
import datetime
import json
import os
import re
import sys
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from sqlalchemy import text

from pipeline.config import ROOT, SOURCE_ID_COLUMNS, get_pg_engine, setting

# ── Source files -> raw_* tables ──────────────────────────────────────────────

DATA_PATH = str(ROOT / "data")
engine = None  # created by load_sources()


def _make_unique_columns(orig_cols):
    """Keep source headers while ensuring uniqueness and non-empty names."""
    mapping = []
    seen = {}
    new_cols = []
    for i, c in enumerate(orig_cols):
        orig = "" if c is None else str(c)
        base = orig
        if base.strip() == "":
            base = f"unnamed_{i+1}"
        candidate = base
        suffix = 1
        while candidate in seen:
            suffix += 1
            candidate = f"{base}_{suffix}"
        seen[candidate] = True
        new_cols.append(candidate)
        mapping.append({"index": i, "original": orig, "column": candidate})
    return new_cols, mapping

def process_task(file_name, table_name, skip_rows, dry_run=False):
    full_path = os.path.join(DATA_PATH, file_name)
    if not os.path.exists(full_path):
        print(f"⚠️ {file_name} 파일을 찾을 수 없습니다.")
        return False

    print(f"\n🚀 작업 시작: {file_name}")

    ext = os.path.splitext(file_name)[1].lower()

    df = None
    mapping = None
    used_enc = None
    used_sep = None

    # Excel files
    if ext in ('.xls', '.xlsx'):
        try:
            # Read without header to detect correct header row robustly
            raw = pd.read_excel(full_path, header=None, dtype=str).fillna('')
            header_idx = None
            max_search = min(len(raw), 50)
            for idx in range(0, max_search):
                row = raw.iloc[idx].astype(str).tolist()
                # treat 'nan' as empty
                parsed = [c if c != 'nan' else '' for c in row]
                non_numeric = sum(1 for v in parsed if v and not re.fullmatch(r"[0-9\-\.]+", v.strip()))
                lengths = [len(v) for v in parsed]
                max_len = max(lengths) if lengths else 0
                avg_len = sum(lengths) / max(1, len(lengths))
                long_cells = sum(1 for L in lengths if L > 100)
                if len(parsed) > 1 and (non_numeric / max(1, len(parsed))) >= 0.6 and max_len < 200 and avg_len < 80 and (long_cells / max(1, len(parsed))) <= 0.2:
                    header_idx = idx
                    break
            if header_idx is None:
                # fallback: use skip_rows if provided
                header_idx = max(0, skip_rows)
            df = pd.read_excel(full_path, header=header_idx, dtype=str)
            df.columns, mapping = _make_unique_columns(df.columns)
            used_enc, used_sep = 'excel', None
        except Exception as e:
            print(f"   ❌ 적재 실패(Excel): {e}")
            return False
    else:
        # CSV files: detect encoding and separator, preserve header
        encodings = ['utf-8-sig', 'cp949', 'utf-16', 'euc-kr']
        separators = [',', '\t']

        for enc in encodings:
            try:
                with open(full_path, 'r', encoding=enc, errors='replace') as f:
                    lines = f.readlines()
            except Exception:
                continue

            for sep in separators:
                # search header candidate lines from the top (prefer earlier header rows)
                header_idx = None
                max_search = min(len(lines), 50)
                search_indices = list(range(0, max_search))

                for idx in search_indices:
                    if idx < 0 or idx >= len(lines):
                        continue
                    candidate = lines[idx].strip('\n').strip()
                    if not candidate:
                        continue
                    try:
                        parsed_candidate = next(csv.reader([candidate], delimiter=sep))
                    except Exception:
                        continue
                    if len(parsed_candidate) <= 1:
                        continue
                    def is_numeric(s):
                        return re.fullmatch(r"[0-9\-\.]+", s.strip()) is not None
                    non_numeric_count = sum(1 for v in parsed_candidate if v and not is_numeric(str(v)))
                    # additional heuristics: reject candidate rows that contain very long text
                    lengths = [len(str(v)) for v in parsed_candidate]
                    max_len = max(lengths) if lengths else 0
                    avg_len = sum(lengths) / max(1, len(lengths))
                    long_cells = sum(1 for L in lengths if L > 100)
                    # Accept header if majority non-numeric, and not dominated by very long text
                    if non_numeric_count / max(1, len(parsed_candidate)) >= 0.6 and max_len < 200 and avg_len < 80 and (long_cells / max(1, len(parsed_candidate))) <= 0.2:
                        header_idx = idx
                        parsed = parsed_candidate
                        break

                if header_idx is None:
                    continue

                try:
                    # Use the detected header row so pandas uses that row as column names
                    temp_df = pd.read_csv(full_path, encoding=enc, sep=sep, header=header_idx)
                except Exception:
                    continue

                # If pandas didn't pick up the parsed header correctly, override it
                if temp_df.shape[1] == len(parsed):
                    temp_df.columns = parsed
                temp_df.columns, mapping = _make_unique_columns(temp_df.columns)
                df = temp_df
                used_enc, used_sep = enc, sep
                break

            if df is not None:
                break

    if df is None:
        print("   ❌ 적재 실패: 인코딩 또는 구분자를 찾을 수 없습니다.")
        return False

    # Ensure mapping exists when headers were not customized earlier
    if mapping is None:
        df.columns, mapping = _make_unique_columns(df.columns)

    # store mapping for traceability
    mapping_dir = os.path.join(DATA_PATH, 'column_mappings')
    os.makedirs(mapping_dir, exist_ok=True)
    map_path = os.path.join(mapping_dir, f"{table_name}_columns.json")
    try:
        with open(map_path, 'w', encoding='utf-8') as mf:
            json.dump({'table': table_name, 'file': file_name, 'mapping': mapping}, mf, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ⚠️ 매핑 파일 저장 실패: {e}")

    # If dry_run, just print summary and mapping path
    if dry_run:
        print(f"   DRY RUN: {file_name} -> {table_name}")
        print(f"   Detected encoding/sep: {used_enc} / {used_sep}")
        print(f"   Rows: {len(df)} Columns: {len(df.columns)}")
        print(f"   Column mapping saved to: {map_path}")
        print('   Sample normalized cols:', df.columns.tolist()[:10])
        return True

    # Ensure table name is safe (alphanumeric + underscore)
    if not re.fullmatch(r"[A-Za-z0-9_]+", table_name):
        print(f"   ❌ 잘못된 테이블 이름: {table_name}")
        return False

    try:
        # Drop existing table completely to ensure full rewrite
        # write table
        # Replace missing values with empty string to avoid literal 'None'/'nan' being written
        df = df.fillna("")
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
            df.to_sql(table_name, conn, if_exists='replace', index=False, chunksize=500)
        print(f"   ✅ 성공! ({used_enc} / '{used_sep}' / {len(df)}행)")
        print(f"   Column mapping saved to: {map_path}")
        return True
    except Exception as e:
        print(f"   ❌ 오류 발생: {e}")
        return False

# 작업 리스트
tasks = [
    ("사건-세부장소_연결정보_260410.csv", "raw_event_place_link", 8),
    ("사건정보_260410.csv", "raw_event_info", 8),
    ("서지정보_260410.csv", "raw_bibliography", 8),
    ("세부장소_260410.csv", "raw_detail_place", 8),
    ("출처정보_260410.xlsx", "raw_source_info", 8),
    ("탄압기구(경찰).csv", "raw_oppression_org_police", 7),
    ("탄압기구(군대).csv", "raw_oppression_org_military", 7),
    ("탄압기구(헌병)_20200131.csv", "raw_oppression_org_gendarme", 7),
    ("행정구역_20200131.xlsx", "raw_admin_region", 8)
]

def load_sources() -> None:
    """Recreate every raw_* table from its source file in data/."""
    global engine
    engine = get_pg_engine()
    for file, table, skip in tasks:
        if not process_task(file, table, skip):
            raise RuntimeError(f"Source ingestion failed: {table}; build stopped.")
    print("\n✨ 모든 테이블이 CSV 헤더와 100% 일치하게 재생성 및 적재되었습니다!")


# ── Row-level TEI ─────────────────────────────────────────────────────────────
DATA_DIR = str(ROOT / 'data')

PGHOST = setting('PG_HOST', 'localhost')
PGPORT = int(setting('PG_PORT', '5432'))
PGUSER = setting('PG_USER', 'postgres')
PGPASSWORD = setting('PG_PASSWORD', '')
PGDATABASE = setting('PG_DATABASE', 'postgres')
TEI_TABLES = os.getenv('TEI_TABLES', '').strip()
TEI_TABLE_PREFIX = os.getenv('TEI_TABLE_PREFIX', 'raw_').strip()

ENCODINGS = ['utf-8-sig', 'cp949', 'utf-16', 'euc-kr']

TAGGABLE_COLS = {
    '사건명', '문서요약', '판결문', '판결문설명', '제목', '비고', '참고노트',
    '관련인물', '피고인', '작성자', '발신자', '수신자', '수신자2',
    '시위_행정구역명', '시위_시작일자', '시위_종료일자',
    '작성일', '발신일', '수신일', '수신일2'
}

TABLE_TAGGABLE_COLS = {
    'raw_detail_place': {'명칭', '이칭', '행정구역명', '상태_참고', '비고', '참고노트'},
}

TABLE_ENTITY_TAGS = {
    'raw_detail_place': {'placeName'},
}


def qname(name):
    # normalize filename -> table name
    name = re.sub(r"\.[^.]+$", '', name)
    name = re.sub(r"[^0-9a-zA-Z]+", '_', name)
    return name.lower()


def _read_csv_rows(path):
    for enc in ENCODINGS:
        try:
            with open(path, newline='', encoding=enc, errors='replace') as fh:
                reader = csv.DictReader(fh)
                headers = reader.fieldnames or []
                rows = list(reader)
            return headers, rows
        except Exception:
            continue
    return [], []


def list_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    tables = [r[0] for r in cur.fetchall()]
    cur.close()
    if TEI_TABLES:
        wanted = [t.strip() for t in TEI_TABLES.split(',') if t.strip()]
        return [t for t in tables if t in wanted]
    if TEI_TABLE_PREFIX:
        return [t for t in tables if t.startswith(TEI_TABLE_PREFIX)]
    return tables


def get_table_columns(conn, table):
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    cols = [r[0] for r in cur.fetchall()]
    cur.close()
    return cols


def _normalize_colname(name):
    return re.sub(r"\s+", "", str(name)).lower()


def _find_columns_by_keywords(cols, keywords):
    hits = []
    norm_cols = [(c, _normalize_colname(c)) for c in cols]
    norm_keywords = [_normalize_colname(k) for k in keywords]
    for col, ncol in norm_cols:
        for kw in norm_keywords:
            if kw and kw in ncol:
                hits.append(col)
                break
    return hits


def _select_column_values(conn, table, cols):
    if not cols:
        return []
    cur = conn.cursor()
    cols_sql = ','.join([f'"{c}"' for c in cols])
    cur.execute(f'SELECT {cols_sql} FROM "{table}"')
    rows = cur.fetchall()
    cur.close()
    return rows


def _split_names(value):
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r'[;,]', text)
    return [p.strip() for p in parts if p and p.strip()]


def build_entity_catalog(conn):
    entities = []
    tables = set(list_tables(conn))

    # Persons
    if 'raw_event_info' in tables:
        cols = get_table_columns(conn, 'raw_event_info')
        person_cols = _find_columns_by_keywords(cols, ['관련인물', '인물', '피고인'])
        for row in _select_column_values(conn, 'raw_event_info', person_cols):
            for val in row:
                for name in _split_names(val):
                    entities.append({'text': name, 'tag': 'persName', 'attrs': {}})

    if 'raw_bibliography' in tables:
        cols = get_table_columns(conn, 'raw_bibliography')
        person_cols = _find_columns_by_keywords(
            cols,
            ['피고인', '관련인물', '인물', '작성자', '발신자', '수신자', '수신자2', '상위자료_작성자']
        )
        for row in _select_column_values(conn, 'raw_bibliography', person_cols):
            for val in row:
                for name in _split_names(val):
                    entities.append({'text': name, 'tag': 'persName', 'attrs': {}})

    # Places
    if 'raw_detail_place' in tables:
        cols = get_table_columns(conn, 'raw_detail_place')
        id_cols = _find_columns_by_keywords(cols, ['세부장소아이디', '장소아이디', 'place_id'])
        name_cols = _find_columns_by_keywords(cols, ['명칭', '장소명', '이름'])
        alias_cols = _find_columns_by_keywords(cols, ['이칭', '별칭'])
        admin_code_cols = _find_columns_by_keywords(cols, ['행정구역코드', '행정코드'])
        admin_name_cols = _find_columns_by_keywords(cols, ['행정구역명', '행정구역'])

        cols_sql = id_cols[:1] + name_cols[:1] + alias_cols[:1] + admin_code_cols[:1] + admin_name_cols[:1]
        for row in _select_column_values(conn, 'raw_detail_place', cols_sql):
            place_id = row[0] if len(row) > 0 else ''
            place_name = row[1] if len(row) > 1 else ''
            alias_val = row[2] if len(row) > 2 else ''
            admin_code = row[3] if len(row) > 3 else ''
            admin_name = row[4] if len(row) > 4 else ''

            pid = str(place_id or '').strip()
            pname = str(place_name or '').strip()
            aliases = _split_names(alias_val)
            acode = str(admin_code or '').strip()
            aname = str(admin_name or '').strip()
            if pid and pname:
                entities.append({'text': pname, 'tag': 'placeName', 'attrs': {'ref': f'#{pid}'}})
            if pid:
                for alias in aliases:
                    entities.append({'text': alias, 'tag': 'placeName', 'attrs': {'ref': f'#{pid}'}})
            if acode and aname:
                entities.append({'text': aname, 'tag': 'placeName', 'attrs': {'ref': f'#{acode}'}})

    # Orgs
    org_tables = {
        'raw_oppression_org_police': 'police',
        'raw_oppression_org_military': 'military',
        'raw_oppression_org_gendarme': 'gendarme'
    }
    for tname, org_type in org_tables.items():
        if tname not in tables:
            continue
        cols = get_table_columns(conn, tname)
        name_cols = _find_columns_by_keywords(cols, ['명칭', '기구명', '기관명', '부서명'])
        if not name_cols and cols:
            name_cols = [cols[1] if len(cols) > 1 else cols[0]]
        for row in _select_column_values(conn, tname, name_cols[:1]):
            org_name = row[0] if row else ''
            name = str(org_name or '').strip()
            if name:
                entities.append({'text': name, 'tag': 'orgName', 'attrs': {'type': org_type}})

    # Events
    if 'raw_event_info' in tables:
        cols = get_table_columns(conn, 'raw_event_info')
        event_cols = _find_columns_by_keywords(cols, ['사건명', '사건'])
        for row in _select_column_values(conn, 'raw_event_info', event_cols[:1]):
            event_name = row[0] if row else ''
            name = str(event_name or '').strip()
            if name:
                entities.append({'text': name, 'tag': 'event', 'attrs': {}})

    # Deduplicate by (tag, text, attrs)
    seen = set()
    unique = []
    for e in entities:
        key = (e['tag'], e['text'], tuple(sorted(e['attrs'].items())))
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    unique.sort(key=lambda x: len(x['text']), reverse=True)
    return unique


def _format_attrs(attrs):
    if not attrs:
        return ''
    parts = []
    for k, v in attrs.items():
        parts.append(f' {k}={quoteattr(str(v))}')
    return ''.join(parts)


def _find_spans(text, entities):
    spans = []

    def overlaps(a_start, a_end):
        for s, e, _ in spans:
            if a_start < e and a_end > s:
                return True
        return False

    for ent in entities:
        needle = ent['text']
        if not needle:
            continue
        start = 0
        while True:
            idx = text.find(needle, start)
            if idx == -1:
                break
            end = idx + len(needle)
            if not overlaps(idx, end):
                attrs = _format_attrs(ent['attrs'])
                repl = f"<{ent['tag']}{attrs}>{escape(needle)}</{ent['tag']}>"
                spans.append((idx, end, repl))
            start = end

    # Dates
    date_patterns = [
        re.compile(r'\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b'),
        re.compile(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일')
    ]
    for pat in date_patterns:
        for m in pat.finditer(text):
            idx, end = m.start(), m.end()
            if overlaps(idx, end):
                continue
            y, mo, d = m.group(1), m.group(2), m.group(3)
            when = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            raw = text[idx:end]
            repl = f"<date when={quoteattr(when)}>{escape(raw)}</date>"
            spans.append((idx, end, repl))

    spans.sort(key=lambda x: x[0])
    return spans


def tag_text(text, entities):
    if text is None:
        return ''
    raw = str(text)
    if not raw:
        return ''
    spans = _find_spans(raw, entities)
    if not spans:
        return escape(raw)

    out = []
    pos = 0
    for start, end, repl in spans:
        if start > pos:
            out.append(escape(raw[pos:start]))
        out.append(repl)
        pos = end
    if pos < len(raw):
        out.append(escape(raw[pos:]))
    return ''.join(out)


def make_tei_for_row(table, rowid, row, headers, entities):
    TEI = ET.Element('TEI')
    teiHeader = ET.SubElement(TEI, 'teiHeader')
    fileDesc = ET.SubElement(teiHeader, 'fileDesc')
    titleStmt = ET.SubElement(fileDesc, 'titleStmt')
    title = ET.SubElement(titleStmt, 'title')
    title.text = f'{table}-{rowid}'
    sourceDesc = ET.SubElement(fileDesc, 'sourceDesc')
    p = ET.SubElement(sourceDesc, 'p')
    # Use timezone-aware UTC timestamp to avoid deprecation warning
    p.text = f'Generated from {table} row {rowid} on {datetime.datetime.now(datetime.timezone.utc).isoformat()}'

    text = ET.SubElement(TEI, 'text')
    body = ET.SubElement(text, 'body')
    div = ET.SubElement(body, 'div', {'type':'record', 'xml:id': f'{table}-{rowid}'})
    taggable_cols = TAGGABLE_COLS | TABLE_TAGGABLE_COLS.get(table, set())
    allowed_tags = TABLE_ENTITY_TAGS.get(table)
    if allowed_tags:
        entities = [entity for entity in entities if entity.get('tag') in allowed_tags]
    for h in headers:
        v = row.get(h, '')
        if v is None:
            v = ''
        p = ET.SubElement(div, 'p')
        p.set('data-col', h)
        if h in taggable_cols:
            tagged = tag_text(v, entities)
        else:
            tagged = escape(str(v))
        wrapper = ET.fromstring(f'<wrapper>{tagged}</wrapper>')
        p.text = wrapper.text
        for child in list(wrapper):
            p.append(child)
    return ET.tostring(TEI, encoding='utf-8').decode('utf-8')


def ensure_connection():
    conn = psycopg2.connect(host=PGHOST, port=PGPORT, user=PGUSER, password=PGPASSWORD, dbname=PGDATABASE)
    conn.autocommit = True
    return conn


def load_table_to_tei(table, conn, entities):
    print('-> table:', table)
    headers = [c for c in get_table_columns(conn, table) if c not in ('tei', 'rowid', 'tei_status')]
    if not headers:
        print('  no columns, skipping', table)
        return
    cur = conn.cursor()

    # ensure rowid, numbered in the order of the source identifier so that a rebuild from
    # the same files gives every record the same rowid (and the same citation key)
    id_col = SOURCE_ID_COLUMNS.get(table)
    order = f'ORDER BY "{id_col}", ctid' if id_col in headers else 'ORDER BY ctid'
    cur.execute(f"ALTER TABLE \"{table}\" ADD COLUMN IF NOT EXISTS rowid bigint;")
    cur.execute(f"UPDATE \"{table}\" SET rowid = gen.rowid FROM (SELECT ctid, row_number() OVER ({order}) AS rowid FROM \"{table}\") gen WHERE \"{table}\".ctid = gen.ctid AND \"{table}\".rowid IS NULL;")

    # add tei column
    cur.execute(f"ALTER TABLE \"{table}\" ADD COLUMN IF NOT EXISTS tei text;")

    # retrieve rowids and full rows to generate TEI
    cur.execute(f'SELECT rowid, {",".join([f"\"{c}\"" for c in headers])} FROM "{table}"')
    allrows = cur.fetchall()
    updates = []
    for rec in allrows:
        rowid = rec[0]
        row = {headers[i]: (rec[i+1] or '') for i in range(len(headers))}
        tei = make_tei_for_row(table, rowid, row, headers, entities)
        updates.append((tei, rowid))
    print('  updating TEI for', len(updates), 'rows')
    execute_values(cur, f'UPDATE "{table}" AS t SET tei = v.tei FROM (VALUES %s) AS v(tei, rowid) WHERE t.rowid = v.rowid', updates)
    cur.close()


def wrap_tei():
    """Add rowid and a TEI rendering of every row to each raw_* table."""
    conn = ensure_connection()
    tables = list_tables(conn)
    if not tables:
        print('No tables found to process')
        conn.close()
        return 1
    entities = build_entity_catalog(conn)
    for t in tables:
        try:
            print('Processing', t)
            load_table_to_tei(t, conn, entities)
        except Exception as e:
            conn.close()
            raise RuntimeError(f'TEI generation failed: {t}; build stopped.') from e
    conn.close()
    print('Done')
    return 0


def main() -> int:
    load_sources()
    return wrap_tei()


if __name__ == '__main__':
    sys.exit(main())
