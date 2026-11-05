"""The systems compared in the evaluation, under one set of shared conditions.

  passage    Passage baseline: records whose text contains an entity string
  hybrid     Hybrid baseline: BM25 + dense (Ollama embedding) -> RRF -> cross-encoder rerank
  graph      Graph-only baseline: triples whose source node name contains an entity string
  dcent      D-CENT: the deployed system over HTTP (docent.php with DOCENT_EVAL_MODE=1): graph facts and
             the hybrid retriever's records, one generation call that lists the facts and then narrates
  no-graph   Ablation: D-CENT without the graph facts (the hybrid baseline's records, D-CENT's generation)
  no-facts   Ablation: D-CENT's evidence, answer without the fact list (narrative only)
  two-call   Ablation: D-CENT's evidence, the fact table in a separate call before the answer (the earlier design)

Shared conditions (fixed, as stated in the paper): one generator for every system, chosen with
LLM_PROVIDER (gemini: GEMINI_MODEL, default the gemini-flash-lite-latest alias; deepseek:
DEEPSEEK_MODEL, default deepseek-chat), temperature 0, top-p 0.8, at most 1,500 output tokens per answer,
an evidence budget of 11,000 characters per question (the deployed system's), and entities
extracted from the question text once by the same model (no system sees the gold entities).
Every system that reads record text searches the same records: register entries, source entries,
and bibliographic records (CORPUS_TABLES), the records the end-to-end system reaches and cites.
"""

from __future__ import annotations

import itertools
import json
import re
import time
from collections import Counter
from typing import Any

import requests

from pipeline.config import ROOT, get_neo4j_driver, get_pg_connection, llm_model, llm_provider, load_secrets, setting
from pipeline.retrieval import CORPUS_TABLES, corpus_tables, hybrid_retrieve, strip_tei

TEMPERATURE = 0.0
TOP_P = 0.8
CONTEXT_CHARS = 11000        # evidence budget per question; the deployed system's DOCENT_CONTEXT_CHARS
ANSWER_MAX_TOKENS = 1500     # output limit of every answer call

MODEL_VERSIONS: Counter = Counter()  # dated model versions reported by the API (the alias can move)

# key -> (label in tables, runner); filled at the end of the module
SYSTEMS: dict[str, tuple[str, Any]] = {}
MAIN_SYSTEMS = ["passage", "hybrid", "graph", "dcent"]
ABLATIONS = ["no-graph", "no-facts", "two-call"]


# ── Generator ────────────────────────────────────────────────────────────────
def _quota_violation(resp: Any) -> str:
    """The quota a 429 names (e.g. 'GenerateRequestsPerDayPerProjectPerModel'), or ''.

    A project that has reached its monthly spending cap is reported as 'MonthlySpendCap', and is
    treated like a daily quota: waiting does not lift it.
    """
    try:
        error = resp.json().get("error", {})
    except ValueError:
        return ""
    if "spending cap" in str(error.get("message", "")).lower():
        return "MonthlySpendCap (PerDay 이상)"
    details = error.get("details", [])
    for detail in details:
        for violation in detail.get("violations", []) or []:
            if violation.get("quotaId") or violation.get("quotaMetric"):
                return str(violation.get("quotaId") or violation.get("quotaMetric"))
    return ""


def _call_deepseek(messages: list[dict[str, str]], model: str, api_key: str, max_tokens: int) -> str:
    """Call DeepSeek OpenAI-compatible Chat Completions API."""
    url = "https://api.deepseek.com/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(4):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=90)
            if resp.status_code == 200:
                data = resp.json()
                MODEL_VERSIONS[data.get("model") or model] += 1
                return data["choices"][0]["message"]["content"].strip()
            elif resp.status_code in (429, 500, 502, 503):
                wait_time = max(15, (attempt + 1) * 10)
                print(f" (재시도 대기 {wait_time}초: {resp.status_code})...", end="", flush=True)
                time.sleep(wait_time)
                continue
            else:
                raise RuntimeError(f"DeepSeek API 호출 실패 (상태 코드 {resp.status_code})")
        except requests.RequestException:
            if attempt == 3:
                raise RuntimeError("DeepSeek API 요청 실패 (연결 오류)") from None
            time.sleep(5)
    raise RuntimeError("DeepSeek API 재시도 한도 초과 (429/503)")


def _call_ollama(messages: list[dict[str, str]], model: str, host: str, max_tokens: int) -> str:
    """Call local Ollama /api/chat endpoint with thinking disabled."""
    url = f"{host.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "stream": False,
        "think": False,
        "options": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "num_predict": max_tokens,
            # Ollama's default context window (2,048-4,096 tokens) silently cuts the front of an
            # 11,000-character evidence prompt; every call gets the same window and seed.
            "num_ctx": int(setting("OLLAMA_NUM_CTX", "16384")),
            "seed": int(setting("OLLAMA_SEED", "42")),
        },
    }
    for attempt in range(4):
        try:
            resp = requests.post(url, json=payload, timeout=300)
            if resp.status_code == 200:
                data = resp.json()
                MODEL_VERSIONS[data.get("model") or model] += 1
                return str(data.get("message", {}).get("content", "")).strip()
            wait_time = max(5, (attempt + 1) * 5)
            print(f" (Ollama 재시도 대기 {wait_time}초: {resp.status_code})...", end="", flush=True)
            time.sleep(wait_time)
        except requests.RequestException:
            if attempt == 3:
                raise RuntimeError("Ollama API 요청 실패 (연결 오류)") from None
            time.sleep(5)
    raise RuntimeError("Ollama API 재시도 한도 초과")


def call_llm(messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
    """Call the configured generator: Gemini, DeepSeek, or local Ollama."""
    temperature, top_p = TEMPERATURE, TOP_P
    max_tokens = max_tokens or ANSWER_MAX_TOKENS
    secrets = load_secrets()
    model = llm_model(secrets)
    prov = llm_provider(secrets)
    if prov == "deepseek":
        ds_key = setting("DEEPSEEK_API_KEY", "", secrets)
        if not ds_key:
            raise RuntimeError("DEEPSEEK_API_KEY가 설정되지 않았습니다.")
        return _call_deepseek(messages, model, ds_key, max_tokens)
    if prov == "ollama":
        host = setting("OLLAMA_HOST", "http://localhost:11434", secrets)
        return _call_ollama(messages, model, host, max_tokens)

    api_key = setting("GEMINI_API_KEY", "", secrets)
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    contents = []
    system_instruction = None
    for msg in messages:
        if msg["role"] == "system":
            system_instruction = {"parts": [{"text": msg["content"]}]}
        else:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "topP": top_p,
            "maxOutputTokens": max_tokens,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = system_instruction

    for attempt in range(4):
        try:
            resp = requests.post(url, json=payload, headers={"x-goog-api-key": api_key}, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                MODEL_VERSIONS[data.get("modelVersion") or model] += 1
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif resp.status_code in (429, 503):
                quota = _quota_violation(resp)
                if "PerDay" in quota:
                    # A daily quota does not recover by waiting a minute; stop instead of failing every answer.
                    raise SystemExit(f"Gemini 사용량 한도에 도달했습니다 ({quota}). 기다려도 풀리지 않습니다. "
                                     "AI Studio에서 한도를 확인한 뒤 새로 실행하세요.")
                wait_time = max(15, (attempt + 1) * 10)
                print(f" (재시도 대기 {wait_time}초: {resp.status_code}{' ' + quota if quota else ''})...", end="", flush=True)
                time.sleep(wait_time)
                continue
            else:
                raise RuntimeError(f"Gemini API 호출 실패 (상태 코드 {resp.status_code})")
        except requests.RequestException:
            if attempt == 3:
                raise RuntimeError("Gemini API 요청 실패 (연결 오류)") from None
            time.sleep(5)

    raise RuntimeError("Gemini API 재시도 한도 초과 (429/503)")


def pack_context(items: list[str], separator: str) -> list[str]:
    """Keep ranked context items until the shared character budget is used up."""
    if CONTEXT_CHARS <= 0:
        return items
    packed: list[str] = []
    used = 0
    for item in items:
        room = CONTEXT_CHARS - used
        if room <= 0:
            break
        packed.append(item[:room])
        used += len(packed[-1]) + len(separator)
    return packed


# ── Evidence helpers ─────────────────────────────────────────────────────────
# Every system that receives record text gets it in blocks identified by table and row, and the
# same citation rule as the deployed system in evaluation mode (includes/prompts.php), so that
# citations can be verified and compared across systems.
CITATION_RULE = json.loads((ROOT / "includes" / "citation_rules.json").read_text(encoding="utf-8"))["ko"]


def record_block(table: str, rowid: Any, text: str) -> str:
    return f'<SOURCE_EVIDENCE id="{table}-{rowid}">\n{text}\n</SOURCE_EVIDENCE>'


def chunk_block(chunk: dict[str, Any]) -> str:
    """A hybrid-baseline chunk ("raw_bibliography:123#0") as a citable record block."""
    table, rowid = chunk["id"].split("#")[0].split(":")
    return record_block(table, rowid, chunk["text"])


_ENTITY_CACHE: dict[str, list[str]] = {}
ENTITY_SYSTEM_PROMPT = (
    "질문에서 검색에 사용할 고유명(인물, 지명, 기관·단체, 사건·문서명)을 질문에 적힌 표기 그대로 추출하십시오. "
    "질문에 없는 이름을 추가하지 말고, 최대 8개를 JSON 문자열 배열로만 출력하십시오."
)


def with_entity_call(res: dict[str, Any], query: str) -> dict[str, Any]:
    """Count the entity-extraction call in the cost of a system that retrieves by name (passage, graph)."""
    res["prompt_tokens"] = res.get("prompt_tokens", 0) + (len(ENTITY_SYSTEM_PROMPT) + len(query)) // 3
    res["llm_calls"] = res.get("llm_calls", 1) + 1
    return res


QUOTED_NAME_RE = re.compile(r"['‘’\"“”「『]([^'‘’\"“”「」『』\n]{2,80})['‘’\"“”」』]")


def question_entities(query: str) -> list[str]:
    """Search entities of the question (one LLM call per question, shared by all pipelines).

    Names the question quotes (incident names such as '3월 2일 전후 평남 평양부 격문 배포') come first and
    are always kept: for questions that name incidents but no person, the model often returns an
    empty list, which left every system that retrieves by name without evidence.
    """
    if query not in _ENTITY_CACHE:
        raw = call_llm([
            {"role": "system", "content": ENTITY_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ], max_tokens=300)
        match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"질의 개체 추출 결과를 해석할 수 없습니다: {raw[:80]}")
        quoted = [m.strip() for m in QUOTED_NAME_RE.findall(query) if m.strip()]
        extracted = [str(e).strip() for e in json.loads(match.group(0)) if str(e).strip()]
        _ENTITY_CACHE[query] = list(dict.fromkeys(quoted + extracted))[:8]
    return _ENTITY_CACHE[query]


# ── Hybrid record retrieval: shared with the D-CENT server (pipeline/retrieval.py) ──────────
# The hybrid baseline and the deployed system read records with the same retriever. When the local
# retrieval service is running (DOCENT_RETRIEVER_URL), the baseline asks it too, so that one index
# and one cross-encoder serve both and the two cannot drift apart; otherwise it builds the index here.
def retrieve_chunks(query: str, top_k: int) -> list[dict[str, Any]]:
    url = setting("DOCENT_RETRIEVER_URL", "").rstrip("/")
    if not url:
        return hybrid_retrieve(query, top_k=top_k, reranker="cross-encoder")
    response = requests.post(f"{url}/search", json={"query": query, "top_k": top_k}, timeout=120)
    response.raise_for_status()
    return [{"id": r["chunk"], "text": r["text"]} for r in response.json()["results"]]


def records_containing(cur: Any, entity: str, limit: int) -> list[tuple[str, int, str]]:
    """Up to `limit` records whose text contains `entity`, as (table, rowid, body text).

    Records are taken in turn from each corpus table, in row order within a table, so that no table
    fills the list before another is reached and the selection does not depend on the database's
    scan order.
    """
    per_table = []
    for table in corpus_tables():
        cur.execute(f'SELECT rowid, tei FROM "{table}" WHERE tei LIKE %s ORDER BY rowid LIMIT %s', (f"%{entity}%", limit))
        per_table.append([(table, rowid, strip_tei(tei)) for rowid, tei in cur.fetchall()])
    out = [r for group in itertools.zip_longest(*per_table) for r in group if r and r[2]]
    return out[:limit]



# ─────────────────────────────────────────────────────────────────────────────
# Passage baseline
# ─────────────────────────────────────────────────────────────────────────────
def retrieve_vector_chunks(query: str, target_entities: list[str], top_k: int = 5) -> list[str]:
    """Retrieve raw text chunks based on lexical/semantic matching from PG raw records."""
    conn = get_pg_connection()
    cur = conn.cursor()
    chunks = []
    for ent in target_entities[:3]:
        for table, rowid, text in records_containing(cur, ent, top_k):
            chunks.append(record_block(table, rowid, text[:400]))
        if len(chunks) >= top_k:
            break
    cur.close()
    conn.close()
    return chunks[:top_k]


def run_vector_rag(query_item: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    chunks = retrieve_vector_chunks(query_item["query"], query_item["target_entities"], top_k=candidate_count(400))
    return with_entity_call(_answer_from_chunks("Vector RAG", query_item, pack_context(chunks, CHUNK_SEPARATOR), time.time() - t0),
                            query_item["query"])


CHUNK_SEPARATOR = "\n---\n"


def candidate_count(item_chars: int, legacy: int = 4) -> int:
    """Enough ranked items of about `item_chars` characters to fill the shared budget."""
    return CONTEXT_CHARS // item_chars + 1 if CONTEXT_CHARS > 0 else legacy


def run_hybrid_vector_rag(query_item: dict[str, Any]) -> dict[str, Any]:
    """BM25 + dense + rerank baseline. Retrieves from the question text only."""
    t0 = time.time()
    search_text = query_item["query"]
    chunks = [chunk_block(c) for c in retrieve_chunks(search_text, top_k=candidate_count(500))]
    return _answer_from_chunks("Hybrid Vector RAG (BM25+Dense+Rerank)", query_item, pack_context(chunks, CHUNK_SEPARATOR), time.time() - t0)


def _answer_from_chunks(model_name: str, query_item: dict[str, Any], chunks: list[str], t_retrieval: float) -> dict[str, Any]:
    context_str = CHUNK_SEPARATOR.join(chunks)
    system_prompt = (
        "You are a strict historical RAG assistant. Use only the retrieved document chunks. "
        "Separate documented facts from inference, never transfer a person or event across regions, "
        "and explicitly state '자료에서 확인되지 않음' when the question is not supported. "
        "Every factual claim must be traceable to a retrieved chunk.\n\n" + CITATION_RULE
    )
    user_prompt = f"[Retrieved Document Chunks]\n{context_str}\n\n[Question]\n{query_item['query']}"

    t_gen_start = time.time()
    try:
        answer = call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
    except Exception as exc:
        answer = f"[Error: {exc}]"
    t_generation = time.time() - t_gen_start

    prompt_chars = len(system_prompt) + len(user_prompt)
    prompt_tokens = prompt_chars // 3  # estimated token count for Korean/English mix
    output_tokens = len(answer) // 3

    return {
        "model": model_name,
        "answer": answer,
        "contexts": chunks,
        "context_chars": len(context_str),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "llm_calls": 1,
        "retrieval_time": round(t_retrieval, 3),
        "generation_time": round(t_generation, 3),
        "total_time": round(t_retrieval + t_generation, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Graph-only baseline
# ─────────────────────────────────────────────────────────────────────────────
def retrieve_graph_triples(target_entities: list[str], limit: int = 15) -> list[str]:
    """Retrieve graph triples from Neo4j knowledge graph without source scoping envelopes."""
    driver = get_neo4j_driver()
    triples = []
    with driver.session() as session:
        for ent in target_entities[:3]:
            cypher = """
            MATCH (n)-[r]->(m)
            WHERE n.name CONTAINS $name OR n.title CONTAINS $name OR n.사건명 CONTAINS $name
            RETURN coalesce(n.name, n.title, n.사건명, labels(n)[0]) AS src,
                   type(r) AS rel,
                   coalesce(m.name, m.title, m.사건명, labels(m)[0]) AS dst
            LIMIT $limit
            """
            res = session.run(cypher, name=ent, limit=limit)
            for r in res:
                triples.append(f"({r['src']}) -[{r['rel']}]-> ({r['dst']})")
            if len(triples) >= limit:
                break
    driver.close()
    return triples[:limit]


def run_graph_rag(query_item: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    triples = pack_context(retrieve_graph_triples(query_item["target_entities"], limit=candidate_count(30, legacy=15)), "\n")
    t_retrieval = time.time() - t0

    context_str = "\n".join(triples)
    system_prompt = (
        "You are a strict historical GraphRAG assistant. Answer only from the graph triples below. "
        "Do not infer an event/person relationship unless the exact triple supports it; "
        "do not merge similarly named places; explicitly state '자료에서 확인되지 않음' when unsupported.\n\n" + CITATION_RULE
    )
    user_prompt = f"[Knowledge Graph Relationships]\n{context_str}\n\n[Question]\n{query_item['query']}"

    t_gen_start = time.time()
    try:
        answer = call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
    except Exception as exc:
        answer = f"[Error: {exc}]"
    t_generation = time.time() - t_gen_start

    prompt_chars = len(system_prompt) + len(user_prompt)
    prompt_tokens = prompt_chars // 3
    output_tokens = len(answer) // 3

    return with_entity_call({
        "model": "Standard GraphRAG",
        "answer": answer,
        "contexts": triples,
        "context_chars": len(context_str),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "llm_calls": 1,
        "retrieval_time": round(t_retrieval, 3),
        "generation_time": round(t_generation, 3),
        "total_time": round(t_retrieval + t_generation, 3),
    }, query_item["query"])


# ─────────────────────────────────────────────────────────────────────────────
# D-CENT: the deployed system over HTTP
# ─────────────────────────────────────────────────────────────────────────────
class DcentClient:
    """Calls docent.php the way static/docent.js does (CSRF token, form posts, retries on 403/429/5xx)."""

    def __init__(self, url: str):
        if not url:
            raise RuntimeError("D-CENT 서버 주소가 없습니다: DOCENT_URL을 secrets.toml 또는 환경변수에 설정하세요 (예: http://localhost:8000/docent.php)")
        self.url = url
        self.cookies: dict[str, str] = {}
        self.token = ""

    def _refresh_token(self, timeout: float = 30.0) -> None:
        resp = requests.get(self.url, params={"csrf": "1"}, cookies=self.cookies, timeout=timeout)
        resp.raise_for_status()
        self.cookies.update(resp.cookies.get_dict())
        self.token = resp.json()["csrf_token"]

    def call(self, action: str, data: dict[str, Any], timeout: float = 180.0) -> Any:
        deadline = time.monotonic() + timeout
        if not self.token:
            self._refresh_token(timeout=max(0.01, deadline - time.monotonic()))
        last = "request deadline exceeded"
        for _ in range(5):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            resp = requests.post(self.url, params={"ajax": action}, data={**data, "csrf_token": self.token},
                                 headers={"X-CSRF-Token": self.token}, cookies=self.cookies, timeout=remaining)
            if resp.status_code == 200:
                try:
                    result = resp.json()
                except ValueError:
                    # PHP printed something other than JSON (a warning, a fatal error, or nothing):
                    # keep the start of the body so that the failure can be traced in answers.json.
                    body = re.sub(r"<[^>]+>", " ", resp.text or "")
                    body = re.sub(r"\s+", " ", body).strip()[:300] or "(빈 응답)"
                    raise RuntimeError(f"D-CENT {action} 응답이 JSON이 아닙니다 (HTTP 200): {body}") from None
                if isinstance(result, dict):
                    MODEL_VERSIONS.update(result.get("model_versions", {}))
                return result
            last = f"{resp.status_code} {resp.text[:400]}"
            if resp.status_code == 403:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._refresh_token(timeout=remaining)
            elif resp.status_code == 429 or resp.status_code >= 500:
                wait = max(0, int(resp.headers.get("Retry-After", "60") or 60)) if resp.status_code == 429 else 10
                if wait >= deadline - time.monotonic():
                    break
                print(f" (D-CENT {action} 재시도, {wait}초 대기)...", end="", flush=True)
                time.sleep(wait)
            else:
                break
        raise RuntimeError(f"D-CENT {action} 실패: {last}")


_DCENT: DcentClient | None = None


def run_dcent(query_item: dict[str, Any], flags: dict[str, str] | None = None, label: str = "D-CENT") -> dict[str, Any]:
    """The deployed answer path: one explain request with the question alone.

    The server computes the graph facts, retrieves the records, and generates. ``flags`` select an
    ablation (no_graph, no_facts, two_call); they exist only in evaluation mode.
    """
    global _DCENT
    if _DCENT is None:
        _DCENT = DcentClient(setting("DOCENT_URL", ""))
    t0 = time.time()
    out = _DCENT.call("explain", {
        "term": query_item["query"], "lang": "ko", "eval": "1",
        "temperature": str(TEMPERATURE), "top_p": str(TOP_P), "max_tokens": str(ANSWER_MAX_TOKENS), **(flags or {}),
    }, timeout=420)
    if "context" not in out:
        raise RuntimeError("explain이 문맥을 반환하지 않았습니다: 서버에 DOCENT_EVAL_MODE=1이 설정되어 있는지 확인하세요")
    total = time.time() - t0
    context = out.get("context", "")
    answer = out.get("text") or "[Error: empty answer]"
    generation = float(out.get("generation_seconds", 0.0) or 0.0)
    res = {
        "model": label,
        "answer": answer,
        "contexts": out.get("context_items", [context] if context else []),
        "context_chars": len(context),
        "graph_chars": int(out.get("graph_chars", 0) or 0),
        "prompt_tokens": int(out.get("prompt_chars", 0)) // 3,
        "output_tokens": (len(answer) + len(out.get("fact_table", "") or "")) // 3,
        "llm_calls": int(out.get("llm_calls", 1) or 1),
        "narrative_used": bool(out.get("graphrag_used")),
        "retrieval_time": round(total - generation, 3),
        "generation_time": round(generation, 3),
        "total_time": round(total, 3),
    }
    # Only the two-call ablation has a fact table; an empty one there means its first call failed.
    if (flags or {}).get("two_call"):
        res["fact_table"] = out.get("fact_table", "")
    return res


SYSTEMS.update({
    "passage": ("Passage baseline", run_vector_rag),
    "hybrid": ("Hybrid baseline", run_hybrid_vector_rag),
    "graph": ("Graph-only baseline", run_graph_rag),
    "dcent": ("D-CENT", lambda item: run_dcent(item, None, "D-CENT")),
    "no-graph": ("D-CENT without graph facts", lambda item: run_dcent(item, {"no_graph": "1"}, "D-CENT without graph facts")),
    "no-facts": ("D-CENT without fact list", lambda item: run_dcent(item, {"no_facts": "1"}, "D-CENT without fact list")),
    "two-call": ("D-CENT, fact table in a separate call", lambda item: run_dcent(item, {"two_call": "1"}, "D-CENT, fact table in a separate call")),
})

SERVER_SYSTEMS = {"dcent", "no-graph", "no-facts", "two-call"}


def resolve(selection: str) -> list[str]:
    """'all' | 'main' | 'ablations' | comma-separated keys -> system keys in table order."""
    groups = {"all": MAIN_SYSTEMS + ABLATIONS, "main": MAIN_SYSTEMS, "ablations": ABLATIONS}
    keys: list[str] = []
    for part in (p.strip() for p in selection.split(",") if p.strip()):
        keys += groups.get(part, [part])
    unknown = [k for k in keys if k not in SYSTEMS]
    if unknown:
        raise SystemExit(f"알 수 없는 시스템: {unknown} (선택: all, main, ablations, {', '.join(SYSTEMS)})")
    return list(dict.fromkeys(keys))
