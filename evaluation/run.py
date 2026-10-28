"""Run the systems on the benchmark and score every answer.

Each run writes a folder results/<date>_<time>[-quick]/ with
  config.json   conditions, dataset, systems, generator model, code version
  answers.json  question, answer, evidence, reference per system (input of rating and judge)
  scores.csv    automatic scores per question and system
  report.md     the report (python -m evaluation report)
Answers are saved after every answer, so an interrupted run continues with --resume.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import datetime as dt
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

from pipeline.config import ROOT, setting

from evaluation import systems
from evaluation.common import BENCHMARK, RESULTS_DIR, read_json, write_json
from evaluation.metrics import METRICS_VERSION, average_precision, evidence_items, clean_answer, is_generation_failure, score_answer

RETRIES = 2              # extra attempts for a failed generation (rate limits)
QUICK_CATEGORIES = 3     # --quick: the first question of each of this many categories

SCORE_COLUMNS = [
    "query_id", "category", "source", "system", "model", "generation_failed",
    "faithfulness", "hallucination_rate", "declined", "entity_hit_rate_at_k", "entity_mrr",
    "cite_ids_valid", "cite_ids_total", "cite_internal_labels", "cite_named_found", "cite_named_total",
    "facts_covered", "facts_total", "context_chars", "prompt_tokens", "output_tokens", "latency_sec",
    "cypher_record_count", "narrative_used", "answer_snippet", "trap_flagged_text",
]


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def _fingerprint(dataset: Path, keys: list[str]) -> dict[str, Any]:
    """Record code/data/environment content, including uncommitted source changes."""
    paths = [ROOT / "requirements.txt", ROOT / "composer.lock", ROOT / "mcp_server.py", ROOT / "docent.php"]
    for directory, pattern in (("pipeline", "*.py"), ("evaluation", "*.py"), ("includes", "*.php"), ("static", "*.js")):
        paths.extend((ROOT / directory).glob(pattern))
    code = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file():
            code.update(str(path.relative_to(ROOT)).encode())
            code.update(path.read_bytes())
    manifest = ROOT / "data/output/build_manifest.json"
    build = read_json(manifest) if manifest.exists() else None
    if build and not build.get("complete"):
        raise SystemExit("데이터 빌드가 미완료 상태입니다. 평가를 시작할 수 없습니다.")
    if build and build.get("stage") == "rdbms" and any(k not in ("passage", "hybrid", "hybrid-two-stage") for k in keys):
        raise SystemExit("PostgreSQL 구축 후 그래프를 아직 갱신하지 않았습니다.")
    if build is None:
        build_id = setting("EVAL_DATA_BUILD_ID", "")
        if not build_id:
            raise SystemExit("데이터 버전이 없습니다. 기존 DB 스냅샷에 EVAL_DATA_BUILD_ID를 지정하세요.")
        build = {"build_id": build_id, "complete": True, "stage": "external"}
    server = _check_server(keys)
    if server and server.get("data_build_id") != build["build_id"]:
        raise SystemExit("서버 DOCENT_DATA_BUILD_ID와 평가 데이터 빌드 ID가 다릅니다.")
    if any(k in ("dcent", "no-paths") for k in keys) and setting("DOCENT_CONTEXT_CHARS", "11000") != str(systems.CONTEXT_CHARS):
        raise SystemExit("DOCENT_CONTEXT_CHARS가 논문의 공통 문맥 예산과 다릅니다.")
    return {
        "code_sha256": code.hexdigest(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "data_build": build,
        "generator": setting("GEMINI_MODEL", "gemini-flash-lite-latest"),
        "python": platform.python_version(),
        "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata["Name"]},
        "server": server,
    }


def _select_questions(items: list[dict[str, Any]], quick: bool, limit: int) -> list[dict[str, Any]]:
    if quick:
        picked, seen = [], set()
        for item in items:
            if item["category"] not in seen:
                seen.add(item["category"])
                picked.append(item)
            if len(picked) == QUICK_CATEGORIES:
                break
        return picked
    return items[:limit] if limit > 0 else items


def _run_one(item: dict[str, Any], key: str, delay: float) -> dict[str, Any]:
    """One system on one question, retrying failed generations."""
    label, runner = systems.SYSTEMS[key]
    res: dict[str, Any] = {}
    for attempt in range(RETRIES + 1):
        if attempt:
            wait = max(delay, 30.0) * attempt
            print(f"    🔁 생성 실패, {wait:.0f}초 후 재시도 ({attempt}/{RETRIES})...", flush=True)
            time.sleep(wait)
        try:
            # Every system that retrieves by name gets the entities extracted from the question text.
            res = runner({**item, "target_entities": systems.question_entities(item["query"])})
        except Exception as exc:
            res = {"answer": f"[Error: {exc}]", "contexts": [], "context_chars": 0, "prompt_tokens": 0,
                   "output_tokens": 0, "total_time": 0.0}
        # A failed fact-table stage means the answer was not produced by the full procedure.
        if not is_generation_failure(res["answer"]) and not is_generation_failure(res.get("fact_table", "ok")):
            res["failed"] = False
            break
    else:
        res["failed"] = True
    res["contexts"] = evidence_items(res.get("contexts", []))
    res["model"] = label
    return res


def _new_run_dir(quick: bool, limit: int) -> Path:
    name = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f") + ("-quick" if quick or limit else "")
    run_dir = RESULTS_DIR / name
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _unfinished_run() -> Path:
    runs = sorted(p for p in RESULTS_DIR.glob("*") if (p / "config.json").exists() and not read_json(p / "config.json").get("complete"))
    if not runs:
        raise SystemExit("이어서 실행할 미완료 실행이 없습니다.")
    return runs[-1]


def _check_server(keys: list[str]) -> dict[str, Any] | None:
    """The dcent systems call a running docent.php in evaluation mode; stop early if it is not set up."""
    if not any(k in ("dcent", "no-paths") for k in keys):
        return
    url = setting("DOCENT_URL", "")
    if not url:
        raise SystemExit(
            "dcent·no-paths 시스템은 실행 중인 웹 도슨트를 호출합니다.\n"
            "  1) 다른 터미널에서:  PHP_CLI_SERVER_WORKERS=4 DOCENT_EVAL_MODE=1 php -S localhost:8000\n"
            "  2) secrets.toml에:   DOCENT_URL = \"http://localhost:8000/docent.php\"\n"
            "  또는 기준 시스템만:  --systems passage,hybrid,graph")
    try:
        import requests
        response = requests.get(url, params={"csrf": "1"}, timeout=10)
        response.raise_for_status()
        metadata = response.json().get("evaluation")
        if not metadata or metadata.get("context_chars") != systems.CONTEXT_CHARS:
            raise ValueError("서버 평가 모드 또는 문맥 예산이 올바르지 않습니다.")
        if metadata.get("model") != setting("GEMINI_MODEL", "gemini-flash-lite-latest"):
            raise ValueError("서버와 평가 생성 모델이 다릅니다.")
        return metadata
    except Exception as exc:
        raise SystemExit(f"DOCENT_URL({url})에 연결할 수 없습니다: {exc}\n"
                         "서버를 PHP_CLI_SERVER_WORKERS=4 DOCENT_EVAL_MODE=1 php -S localhost:8000 으로 띄웠는지 확인하세요.")


def run(quick: bool = False, limit: int = 0, selection: str = "all", dataset: Path | None = None, resume: bool = False) -> Path:
    if resume:
        run_dir = _unfinished_run()
        config = read_json(run_dir / "config.json")
        dataset, keys = Path(config["dataset"]), config["systems"]
        quick, limit = config["quick"], config["limit"]
        answers = [a for a in (read_json(run_dir / "answers.json") if (run_dir / "answers.json").exists() else []) if not a.get("generation_failed")]
        print(f"↩️  {run_dir.name} 이어서 실행 (완료된 답변 {len(answers)}개)")
        if config.get("fingerprint") != _fingerprint(dataset, keys):
            raise SystemExit("코드·데이터·환경·서버 조건이 달라 재개할 수 없습니다. 새 평가를 시작하세요.")
        systems.MODEL_VERSIONS.update(config.get("model_versions", {}))
    else:
        dataset = dataset or BENCHMARK
        if not dataset.exists():
            raise SystemExit(f"{dataset}가 없습니다. 먼저 `python -m evaluation dataset`을 실행하세요.")
        keys = systems.resolve(selection)
        fingerprint = _fingerprint(dataset, keys)
        systems.MODEL_VERSIONS.clear()
        run_dir = _new_run_dir(quick, limit)
        answers = []
        config = {
            "started": dt.datetime.now().isoformat(timespec="seconds"),
            "dataset": str(dataset), "quick": quick, "limit": limit, "systems": keys,
            "labels": {k: systems.SYSTEMS[k][0] for k in keys},
            "conditions": {"temperature": systems.TEMPERATURE, "top_p": systems.TOP_P,
                           "context_chars": systems.CONTEXT_CHARS, "answer_max_tokens": systems.ANSWER_MAX_TOKENS,
                           "retries": RETRIES},
            "generator": setting("GEMINI_MODEL", "gemini-flash-lite-latest"),
            "docent_url": setting("DOCENT_URL", "") if any(k in ("dcent", "no-paths") for k in keys) else "",
            "fingerprint": fingerprint,
            "code_version": _git_commit(), "python": platform.python_version(), "complete": False,
        }
        write_json(run_dir / "config.json", config)

    delay = float(setting("EVAL_DELAY", "15"))
    questions = _select_questions(read_json(dataset), quick, limit)
    done = {(a["query_id"], a["system"]) for a in answers}
    print(f"🚀 {run_dir.name}: 질문 {len(questions)}개 × 시스템 {len(keys)}개 ({', '.join(keys)}), 호출 간격 {delay:.0f}초")

    for n, item in enumerate(questions, 1):
        print(f"\n[{n}/{len(questions)}] [{item['category']}] {item['query']}")
        for key in keys:
            if (item["id"], key) in done:
                continue
            print(f"  🔹 {systems.SYSTEMS[key][0]} ...", end="", flush=True)
            res = _run_one(item, key, delay)
            answers.append({
                "query_id": item["id"], "category": item["category"], "source": item.get("source", ""),
                "system": key, "model": res["model"], "user_input": item["query"],
                "response": res["answer"], "response_cleaned": clean_answer(res["answer"], res.get("contexts", [])),
                "retrieved_contexts": res.get("contexts", []),
                "reference": "\n".join(item.get("ground_truth", item["gold_facts"])),
                "fact_table": res.get("fact_table", ""), "generation_failed": res["failed"],
                "context_chars": res.get("context_chars", 0), "prompt_tokens": res.get("prompt_tokens", 0),
                "output_tokens": res.get("output_tokens", 0), "latency_sec": res.get("total_time", 0.0),
                "cypher_record_count": res.get("cypher_record_count", ""), "narrative_used": res.get("narrative_used", ""),
            })
            print(" ⚠️ 생성 실패" if res["failed"] else f" {res.get('total_time', 0):.1f}s", flush=True)
            write_json(run_dir / "answers.json", answers)
            config["model_versions"] = dict(systems.MODEL_VERSIONS)
            write_json(run_dir / "config.json", config)
            if delay > 0:
                time.sleep(delay)
        write_json(run_dir / "answers.json", answers)  # for --resume
        config["model_versions"] = dict(systems.MODEL_VERSIONS)
        write_json(run_dir / "config.json", config)

    write_scores(run_dir, read_json(dataset))
    config["metrics_version"] = METRICS_VERSION
    config["model_versions"] = dict(systems.MODEL_VERSIONS)  # calls of this harness; the server uses the same key and alias
    config["complete"] = True
    config["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    write_json(run_dir / "config.json", config)
    return run_dir


def write_scores(run_dir: Path, items: list[dict[str, Any]]) -> None:
    """Score every saved answer into scores.csv (can be re-run on an existing run)."""
    by_id = {item["id"]: item for item in items}
    rows = []
    for a in read_json(run_dir / "answers.json"):
        item = by_id[a["query_id"]]
        scores = score_answer(item, {"answer": a["response"], "contexts": a["retrieved_contexts"]})
        rows.append({**{k: a.get(k, "") for k in SCORE_COLUMNS}, **scores,
                     "answer_snippet": a["response"].replace("\n", " ")[:120]})
    with (run_dir / "scores.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCORE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    config_path = run_dir / "config.json"
    if config_path.exists():   # which scoring rules produced scores.csv
        config = read_json(config_path)
        config["metrics_version"] = METRICS_VERSION
        write_json(config_path, config)


# ── Context precision with a local LLM judge (secondary measure) ─────────────
JUDGE_PROMPT = """You are a strict retrieval evaluator.
Judge whether each context is relevant evidence for answering the question and
the reference. Return JSON only, with exactly this shape:
{{"relevant":[0,1,0]}}
Use 1 only when the context contains direct evidence for at least one
reference claim. Use 0 for merely related names, metadata, or unsupported
inference.
Do not use outside knowledge.

QUESTION:
{question}

REFERENCE:
{reference}

There are exactly {context_count} contexts. Return exactly {context_count} values in relevant.

CONTEXTS (in rank order):
{contexts}
"""


def _judge_one(record: dict[str, Any], model: str, host: str) -> dict[str, Any]:
    import json
    import re

    import requests

    contexts = record.get("retrieved_contexts", [])
    result = {"query_id": record["query_id"], "system": record["system"], "model": record["model"]}
    if not contexts:
        return {**result, "relevant": [], "context_precision": 0.0}
    prompt = JUDGE_PROMPT.format(question=record["user_input"], reference=record["reference"], context_count=len(contexts),
                                 contexts="\n".join(f"[{i}] {value}" for i, value in enumerate(contexts)))
    for _ in range(3):
        response = requests.post(f"{host.rstrip('/')}/api/chat", timeout=300, json={
            "model": model, "messages": [{"role": "user", "content": prompt}], "format": "json",
            "stream": False, "options": {"temperature": 0, "num_predict": max(128, len(contexts) * 4 + 32)}, "think": False})
        response.raise_for_status()
        try:
            match = re.search(r"\{.*\}", response.json()["message"]["content"], re.DOTALL)
            parsed = json.loads(match.group(0)) if match else {}
            relevant = parsed.get("relevant", []) if isinstance(parsed, dict) else []
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(relevant, list) and len(relevant) == len(contexts) and all(type(v) is int and v in (0, 1) for v in relevant):
            return {**result, "relevant": relevant, "context_precision": round(average_precision(relevant), 4)}
    raise RuntimeError(f"판정 결과를 해석할 수 없습니다: {record['query_id']} / {record['system']}")


def judge(run_dir: Path) -> Path:
    """Context precision of every answer's evidence -> <run>/judge.json (JUDGE_MODEL on OLLAMA_HOST)."""
    import concurrent.futures

    model = setting("JUDGE_MODEL", "gemma4:26b")
    host = setting("OLLAMA_HOST", "http://localhost:11434")
    records = [r for r in read_json(run_dir / "answers.json") if not r.get("generation_failed")]
    print(f"⚖️  {run_dir.name}: 답변 {len(records)}개의 근거를 {model}로 판정합니다")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda r: _judge_one(r, model, host), records))
    out = run_dir / "judge.json"
    write_json(out, {"judge_model": model, "metric_definition": "average_precision_v2", "results": results})
    return out
