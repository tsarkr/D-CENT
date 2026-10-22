"""D-CENT data pipeline.

  python -m pipeline build            all steps: PostgreSQL/TEI, then the Neo4j graph
  python -m pipeline build rdbms      only the PostgreSQL/TEI steps (1-5)
  python -m pipeline build graph      only the graph steps (6-8), from existing PostgreSQL data
  python -m pipeline build --yes      do not pause for the entity-list review
  python -m pipeline check            offline self-test (no database needed)
  python -m pipeline inspect          list the read-only diagnostics (names, family, search, counts, sparsity, sources)

Settings (database, Neo4j, API keys, THESAURUS_CSV) come from environment variables
or .streamlit/secrets.toml; see README.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from pipeline.config import ENTITIES_CSV, ENTITIES_XLSX, ROOT

# (label, module arguments). Each step runs as `python -m pipeline.<module>` in its own process,
# so a failing step stops the build and memory is released between steps.
RDBMS_STEPS = [
    ("1 원천 파일 적재 + 행 단위 TEI", ["ingest"]),
    ("2 인명 태깅 (한자 독음 ruby, persName)", ["entities", "tag"]),
    ("2 개체 목록 작성", ["entities", "export"]),
    ("3 인명사전 연결 (i815)", ["authority"]),
    ("2 개체 목록 갱신", ["entities", "export"]),  # followed by the review pause
    ("4 CIDOC-CRM 매핑", ["cidoc"]),
    ("5 PostgreSQL 검색 인덱스", ["indexes"]),
]
GRAPH_STEPS = [
    ("6 Neo4j 그래프 구축", ["graph"]),
    ("7 한국역사용어시소러스 적재", ["thesaurus"]),
    ("8 이름 독음·로마자 표기", ["names"]),
]
REVIEW_STEP = "2 개체 목록 갱신"  # the build pauses after this step so the entity list can be reviewed


def _run(label: str, module_args: list[str]) -> None:
    print(f"\n━━ {label}  (python -m pipeline.{' '.join(module_args)})", flush=True)
    t0 = time.time()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")]))}
    result = subprocess.run([sys.executable, "-m", f"pipeline.{module_args[0]}", *module_args[1:]], cwd=ROOT, env=env)
    if result.returncode != 0:
        print(f"\n❌ 실패: {label} (exit {result.returncode}). 원인을 고친 뒤 같은 명령을 다시 실행하세요.")
        sys.exit(result.returncode)
    print(f"━━ 완료: {label} ({time.time() - t0:.0f}s)", flush=True)


def build(stage: str, yes: bool) -> int:
    steps = {"all": RDBMS_STEPS + GRAPH_STEPS, "rdbms": RDBMS_STEPS, "graph": GRAPH_STEPS}[stage]
    print(f"파이프라인 시작: {stage} ({len(steps)}단계)")
    for n, (label, module_args) in enumerate(steps, 1):
        _run(label, module_args)
        if label == REVIEW_STEP and not yes and sys.stdin.isatty():
            sheet = ENTITIES_XLSX if ENTITIES_XLSX.exists() else ENTITIES_CSV
            print(f"\n⏸  개체 목록을 검토하세요: {sheet}\n   계속하려면 Enter, 중단하려면 Ctrl+C.")
            input()
    print("\n✅ 파이프라인 완료")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="build PostgreSQL/TEI and the Neo4j graph")
    p_build.add_argument("stage", nargs="?", choices=["all", "rdbms", "graph"], default="all")
    p_build.add_argument("--yes", action="store_true", help="do not pause for the entity-list review")
    sub.add_parser("check", help="offline self-test")
    p_inspect = sub.add_parser("inspect", help="read-only diagnostics of the databases")
    p_inspect.add_argument("what", nargs="?", help="names | family | search | counts | sparsity | sources")
    p_inspect.add_argument("terms", nargs="*", help="question, name(s) to look up")
    args = parser.parse_args()

    if args.command == "build":
        return build(args.stage, args.yes)
    if args.command == "check":
        from pipeline.check import main as check_main
        return check_main()
    from pipeline.inspect import main as inspect_main
    return inspect_main(args.what, args.terms)


if __name__ == "__main__":
    sys.exit(main())
