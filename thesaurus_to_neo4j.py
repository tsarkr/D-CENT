#!/usr/bin/env python3
"""Load the Korean History Thesaurus into Neo4j."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent

from scripts.config import load_secrets, setting, SECRETS

DEFAULT_CSV = ROOT / "data" / "교육부 국사편찬위원회_한국역사용어시소러스 정보_20211028.csv"
TARGET_PERIODS = {"일제시기", "근대", "근대-현대"}
REQUIRED_COLUMNS = {
    "term_id",
    "term_name",
    "term_ch",
    "term_times",
    "term_lk",
    "term_desc",
}


def read_thesaurus_csv(csv_path: Path) -> list[dict[str, str]]:
    """Read and normalize only the requested historical periods."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {csv_path}")

    rows_by_id: dict[str, dict[str, str]] = {}
    filtered_count = 0
    duplicate_count = 0
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            columns = set(reader.fieldnames or [])
            missing_columns = REQUIRED_COLUMNS - columns
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise ValueError(f"CSV 필수 컬럼이 없습니다: {missing}")

            for line_number, source_row in enumerate(reader, start=2):
                row = {
                    column: str(source_row.get(column) or "").strip()
                    for column in REQUIRED_COLUMNS
                }
                if row["term_times"] not in TARGET_PERIODS:
                    continue
                filtered_count += 1
                if not row["term_id"]:
                    print(
                        f"경고: {line_number}행은 term_id가 없어 건너뜁니다.",
                        file=sys.stderr,
                    )
                    continue
                if row["term_id"] in rows_by_id:
                    duplicate_count += 1
                rows_by_id[row["term_id"]] = {
                    "term_id": row["term_id"],
                    "name": row["term_name"],
                    "hanja": row["term_ch"],
                    "category": row["term_lk"],
                    "description": row["term_desc"],
                    "period": row["term_times"],
                }
    except UnicodeDecodeError as exc:
        raise ValueError(f"UTF-8 CSV로 읽을 수 없습니다: {csv_path}") from exc
    except csv.Error as exc:
        raise ValueError(f"CSV 형식 오류 ({csv_path}): {exc}") from exc

    if duplicate_count:
        print(f"경고: 중복 term_id {duplicate_count}건은 마지막 행을 사용합니다.")
    print(f"시대 필터 결과: {filtered_count}건, 적재 대상: {len(rows_by_id)}건")
    return list(rows_by_id.values())


SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Thesaurus) REQUIRE t.term_id IS UNIQUE",
    "CREATE INDEX IF NOT EXISTS FOR (t:Thesaurus) ON (t.name)",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (c:ThesaurusCategory) REQUIRE c.path IS UNIQUE",
)

UPSERT_QUERY = """
UNWIND $rows AS row
MERGE (t:Thesaurus {term_id: row.term_id})
SET t.name = row.name,
    t.hanja = row.hanja,
    t.category = row.category,
    t.description = row.description,
    t.period = row.period,
    t.type = '시소러스'
WITH t
CALL {
    WITH t
    MATCH (target)
        WHERE (target:Place OR target:Person OR target:장소 OR target:인물)
            AND (coalesce(target.name, '') <> '' OR coalesce(target.명칭, '') <> '')
            AND (
                    target.name = t.name OR target.name = t.hanja OR
                    target.명칭 = t.name OR target.명칭 = t.hanja
            )
    SET target.description = t.description,
        target.hanja = t.hanja
    MERGE (target)-[:DEFINED_AS]->(t)
    RETURN count(target) AS matched_targets
}
RETURN count(t) AS upserted
"""


# The thesaurus is a hierarchy: term_lk holds each term's classification path
# ("교육>근대교육기관>초등교육기관"). Loading it as broader-term links keeps
# terms that match no Person/Place connected to the rest of the thesaurus.
CATEGORY_QUERY = """
UNWIND $rows AS row
MERGE (c:ThesaurusCategory:분류 {path: row.path})
SET c.name = row.name, c.명칭 = row.name, c.type = '시소러스분류'
WITH c, row
WHERE row.parent IS NOT NULL
MERGE (parent:ThesaurusCategory:분류 {path: row.parent})
MERGE (c)-[:P127_has_broader_term]->(parent)
"""

TERM_CATEGORY_QUERY = """
UNWIND $rows AS row
MATCH (t:Thesaurus {term_id: row.term_id})
MATCH (c:ThesaurusCategory {path: row.path})
MERGE (t)-[:P127_has_broader_term]->(c)
"""


def category_path(category: str) -> list[str]:
    return [part.strip() for part in category.split(">") if part.strip()]


def category_records(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return (category nodes ordered parents-first, term -> category links)."""
    categories: dict[str, dict[str, Any]] = {}
    term_links = []
    for row in rows:
        parts = category_path(row["category"])
        if not parts:
            continue
        for depth in range(1, len(parts) + 1):
            path = ">".join(parts[:depth])
            categories.setdefault(path, {
                "path": path,
                "name": parts[depth - 1],
                "parent": ">".join(parts[:depth - 1]) or None,
            })
        term_links.append({"term_id": row["term_id"], "path": ">".join(parts)})
    ordered = sorted(categories.values(), key=lambda c: c["path"].count(">"))
    return ordered, term_links


def load_categories(session: Any, rows: list[dict[str, str]], batch_size: int) -> None:
    categories, term_links = category_records(rows)
    session.run(CATEGORY_QUERY, rows=categories).consume()
    for start in range(0, len(term_links), batch_size):
        session.run(TERM_CATEGORY_QUERY, rows=term_links[start : start + batch_size]).consume()
    print(f"시소러스 분류 계층: 분류 {len(categories)}개, 용어 연결 {len(term_links)}건", flush=True)


def ensure_schema(session: Any) -> None:
    for statement in SCHEMA_STATEMENTS:
        session.run(statement).consume()


def load_batches(driver: Any, rows: list[dict[str, str]], batch_size: int) -> int:
    written = 0
    with driver.session() as session:
        ensure_schema(session)
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            result = session.run(UPSERT_QUERY, rows=batch).single()
            upserted = int(result["upserted"]) if result else 0
            if upserted != len(batch):
                raise RuntimeError(
                    f"Neo4j 적재 개수 불일치: 입력 {len(batch)}건, 처리 {upserted}건"
                )
            written += upserted
            print(f"Neo4j 적재: {written}/{len(rows)}건", flush=True)
        load_categories(session, rows, batch_size)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="한국역사용어시소러스의 근대·일제강점기 데이터를 Neo4j에 적재합니다."
    )
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="시소러스 CSV 파일 경로")
    parser.add_argument(
        "--batch-size", type=int, default=1000, help="Neo4j 트랜잭션 배치 크기"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch-size는 1 이상이어야 합니다.")

    rows = read_thesaurus_csv(Path(args.csv).expanduser())
    if not rows:
        print("필터링된 데이터가 없어 종료합니다.")
        return 0

    password = setting("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("NEO4J_PASSWORD를 환경변수 또는 secrets.toml에 설정하세요.")

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        setting("NEO4J_URI", "bolt://localhost:7687"),
        auth=(setting("NEO4J_USER", "neo4j"), password),
    )
    try:
        driver.verify_connectivity()
        load_batches(driver, rows, args.batch_size)
        print("Neo4j 적재 완료.", flush=True)
    finally:
        print("Neo4j 연결 종료 중...", flush=True)
        driver.close()
        print("작업이 정상적으로 종료되었습니다.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("사용자에 의해 중단되었습니다.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"오류: {exc}", file=sys.stderr)
        raise SystemExit(1)