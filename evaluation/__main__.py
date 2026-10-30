"""D-CENT evaluation.

  python -m evaluation dataset [--generate]     validate questions -> dataset/benchmark.json
  python -m evaluation dataset --holdout [--seed N]
                                                held-out set (incidents unused by the development set)
                                                -> dataset/holdout/benchmark.json
  python -m evaluation run [--quick | --limit N] [--systems all|main|ablations|a,b] [--dataset PATH] [--resume]
                                                run the systems, score, and write the report
  python -m evaluation report [RUN ...] [--rescore]
                                                report of the latest run, or of several runs combined;
                                                --rescore re-scores the saved answers with the current rules
  python -m evaluation judge [RUN]              context precision with a local LLM judge (secondary)
  python -m evaluation expert export|score      historians validate the extracted entities and relations
  python -m evaluation rate export [RUN] [--n N] | rate score
                                                historians rate the answers of a run blind

RUN is a folder name under evaluation/results/ (default: the latest run).
Settings come from environment variables or .streamlit/secrets.toml: GEMINI_API_KEY, GEMINI_MODEL,
DOCENT_URL (docent.php running with DOCENT_EVAL_MODE=1, for the dcent and no-paths systems),
EVAL_DELAY (seconds between calls, default 15), JUDGE_MODEL and OLLAMA_HOST (judge).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evaluation.common import RESULTS_DIR, latest_run


def _run_dir(name: str | None) -> Path:
    if not name:
        return latest_run()
    path = Path(name) if Path(name).is_dir() else RESULTS_DIR / name
    if not path.is_dir():
        raise SystemExit(f"실행 폴더가 없습니다: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dataset", help="validate (and optionally regenerate) the benchmark")
    p.add_argument("--generate", action="store_true", help="regenerate the template questions from the event register first")
    p.add_argument("--holdout", action="store_true", help="generate the held-out set instead (dataset/holdout/)")
    p.add_argument("--seed", type=int, default=None, help="seed for the held-out set (default 2026)")
    p.add_argument("--count", type=int, default=115, help="questions to generate for the held-out set before validation (default 115, about 100 pass)")

    p = sub.add_parser("run", help="run the systems on the benchmark")
    p.add_argument("--quick", action="store_true", help="one question from each of three categories")
    p.add_argument("--limit", type=int, default=0, help="only the first N questions")
    p.add_argument("--systems", default="all", help="all (default), main, ablations, or comma-separated keys")
    p.add_argument("--resume", action="store_true", help="continue the latest unfinished run")
    p.add_argument("--dataset", type=Path, default=None, help="question file (default: dataset/benchmark.json)")

    p = sub.add_parser("report", help="write the report of one or more runs")
    p.add_argument("runs", nargs="*")
    p.add_argument("--rescore", action="store_true", help="re-score the saved answers with the current scoring rules first")

    p = sub.add_parser("judge", help="context precision with a local LLM judge")
    p.add_argument("run", nargs="?")

    p = sub.add_parser("expert", help="expert validation of the graph (export | score)")
    p.add_argument("action", choices=["export", "score"])

    p = sub.add_parser("rate", help="blind rating of answers (export | score)")
    p.add_argument("action", choices=["export", "score"])
    p.add_argument("run", nargs="?", help="run to export (default: the latest)")
    p.add_argument("--n", type=int, default=0, help="rate only N questions, sampled across categories (default: all)")

    args = parser.parse_args()

    if args.command == "dataset":
        if args.holdout:
            from evaluation.benchmark import HOLDOUT_SEED, build_holdout
            return build_holdout(args.count, HOLDOUT_SEED if args.seed is None else args.seed)
        from evaluation.benchmark import build
        return build(args.generate)
    if args.command == "run":
        if args.quick and args.limit:
            parser.error("--quick과 --limit은 함께 쓸 수 없습니다.")
        from evaluation.report import report
        from evaluation.run import run
        report([run(args.quick, args.limit, args.systems, dataset=args.dataset, resume=args.resume)])
        return 0
    if args.command == "report":
        from evaluation.report import report
        report([_run_dir(name) for name in args.runs] or [latest_run()], rescore_first=args.rescore)
        return 0
    if args.command == "judge":
        from evaluation.report import report
        from evaluation.run import judge
        run_dir = _run_dir(args.run)
        judge(run_dir)
        report([run_dir])
        return 0
    if args.command == "expert":
        from evaluation.human import expert_export, expert_score
        return expert_export() if args.action == "export" else expert_score([])
    from evaluation.human import rate_export, rate_score
    return rate_export(_run_dir(args.run), n_questions=args.n) if args.action == "export" else rate_score([])


if __name__ == "__main__":
    sys.exit(main())
