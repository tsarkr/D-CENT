#!/usr/bin/env python3
"""Diagnose the namesIndex full-text retrieval path used by docent.php.

This script is read-only against Neo4j. It prints the definition and state of
the ``namesIndex`` full-text index and the top results for a question, which is
the same lookup the PHP graph action uses as its cross-check.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent

from scripts.config import load_secrets, setting, SECRETS

DEFAULT_QUERY = "보성사에서 손병희를 명예총재로, 이종일을 단장으로 결성된 천도교 항일비밀결사는?"
TARGET_NAME = "천도구국단"
TOP_K = 5


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Neo4j GraphRAG 검색 경로 진단")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="진단에 사용할 질문")
    parser.add_argument("--target-name", default=TARGET_NAME, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global TARGET_NAME
    TARGET_NAME = args.target_name
    password = setting("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("NEO4J_PASSWORD를 환경변수 또는 .streamlit/secrets.toml에 설정하세요.")

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        setting("NEO4J_URI", "bolt://localhost:7687"),
        auth=(setting("NEO4J_USER", "neo4j"), password),
    )
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            fulltext_results(session, args.query)
    finally:
        driver.close()
    print("\n진단 완료")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("사용자에 의해 중단되었습니다.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"진단 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
