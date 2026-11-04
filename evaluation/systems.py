"""The systems compared in the evaluation, under one set of shared conditions.

  passage           Passage baseline: records whose text contains an entity string
  hybrid            Hybrid baseline: BM25 + dense (Ollama embedding) -> RRF -> cross-encoder rerank
  graph             Graph-only baseline: triples whose source node name contains an entity string
  dcent             D-CENT end to end: the deployed system over HTTP (docent.php with DOCENT_EVAL_MODE=1)
  controlled        D-CENT, controlled evidence: name-matched triples and records, two-stage generation
  no-paths          Ablation A: the end-to-end system without narrative paths
  one-stage         Ablation B: controlled D-CENT evidence answered in one call
  hybrid-two-stage  Ablation C: the hybrid baseline's evidence with the two-stage generation

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
ABLATIONS = ["controlled", "no-paths", "one-stage", "hybrid-two-stage"]


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


QUOTED_NAME_RE = re.compile(r"['‘’\"“”「『]([^'‘’\"“”「」『』\n]{2,80})['‘’\"“”」』]")


def question_entities(query: str) -> list[str]:
    """Search entities of the question (one LLM call per question, shared by all pipelines).

    Names the question quotes (incident names such as '3월 2일 전후 평남 평양부 격문 배포') come first and
    are always kept: for questions that name incidents but no person, the model often returns an
    empty list, which left every system that retrieves by name without evidence.
    """
    if query not in _ENTITY_CACHE:
        raw = call_llm([
            {"role": "system", "content": (
                "질문에서 검색에 사용할 고유명(인물, 지명, 기관·단체, 사건·문서명)을 질문에 적힌 표기 그대로 추출하십시오. "
                "질문에 없는 이름을 추가하지 말고, 최대 8개를 JSON 문자열 배열로만 출력하십시오."
            )},
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
    return _answer_from_chunks("Vector RAG", query_item, pack_context(chunks, CHUNK_SEPARATOR), time.time() - t0)


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

    return {
        "model": "Standard GraphRAG",
        "answer": answer,
        "contexts": triples,
        "context_chars": len(context_str),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "retrieval_time": round(t_retrieval, 3),
        "generation_time": round(t_generation, 3),
        "total_time": round(t_retrieval + t_generation, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# D-CENT with controlled evidence assembly, and the generation ablations
# ─────────────────────────────────────────────────────────────────────────────
def retrieve_docent_scoped_context(target_entities: list[str]) -> str:
    """Retrieve Neo4j graph triples + PostgreSQL primary sources wrapped in <SOURCE_EVIDENCE> envelopes."""
    driver = get_neo4j_driver()
    conn = get_pg_connection()
    cur = conn.cursor()

    envelopes = []

    # 1. Graph subgraph context
    triples = []
    with driver.session() as session:
        for ent in target_entities[:3]:
            cypher = """
            MATCH (n)-[r]->(m)
            WHERE n.name CONTAINS $name OR n.title CONTAINS $name OR n.사건명 CONTAINS $name
            RETURN coalesce(n.name, n.title, n.사건명, labels(n)[0]) AS src,
                   type(r) AS rel,
                   coalesce(m.name, m.title, m.사건명, labels(m)[0]) AS dst
            LIMIT 8
            """
            res = session.run(cypher, name=ent)
            for r in res:
                triples.append(f"({r['src']}) -[{r['rel']}]-> ({r['dst']})")
    driver.close()

    if triples:
        envelopes.append("<GRAPH_TOPOLOGY>\n" + "\n".join(triples) + "\n</GRAPH_TOPOLOGY>")

    # 2. Scoped PostgreSQL primary sources (more candidates when a shared budget decides the cut-off)
    n_entities, per_entity = (3, max(4, CONTEXT_CHARS // 1500)) if CONTEXT_CHARS > 0 else (2, 2)
    for ent in target_entities[:n_entities]:
        for table, rowid, text in records_containing(cur, ent, per_entity):
            envelopes.append(
                f'<SOURCE_EVIDENCE id="{table}-{rowid}" entity="{ent}">\n'
                f'  [사료 원문: {text[:500]}]\n'
                f'</SOURCE_EVIDENCE>'
            )

    cur.close()
    conn.close()
    return "\n\n".join(pack_context(envelopes, "\n\n"))


def run_docent_hybrid_rag(
    query_item: dict[str, Any],
    model_name: str = "D-CENT (controlled evidence)",
    scoped_context: str | None = None,
    two_stage: bool = True,
) -> dict[str, Any]:
    """Proposed pipeline. `scoped_context` and `two_stage` are only set by the ablations below."""
    t0 = time.time()
    if scoped_context is None:
        scoped_context = retrieve_docent_scoped_context(query_item["target_entities"])
    t_retrieval = time.time() - t0

    # ── Stage 1: Fact Table Extraction ──
    ext_sys = (
        "당신은 한국 근현대사 1차 사료 분석 전문가입니다. "
        "제공된 <SOURCE_EVIDENCE> 사료 블록들을 사료 비판적으로 정밀 분석하여, "
        "각 사료별 사실관계를 왜곡이나 교차 귀속(인물/지역 혼합) 없이 다음 정밀 팩트 표로 추출하십시오.\n"
        "반드시 각 블록에 명시된 사실만 기록하고, 타 지역 사건이나 무관한 인물을 절대 섞지 마십시오."
    )
    ext_user = f"다음 사료군에서 [사료 ID | 대상 개체 | 발생 장소/지역 | 실제 행동 인물 | 사료에 기록된 핵심 팩트(1~2줄)] 표를 마크다운 표로 추출하십시오.\n\n[사료 원문 블록]\n{scoped_context}"

    t_gen_start = time.time()
    fact_block = ""
    if two_stage:
        try:
            fact_table = call_llm([
                {"role": "system", "content": ext_sys},
                {"role": "user", "content": ext_user}
            ], max_tokens=1000)
        except Exception as exc:
            fact_table = f"[Fact Extraction Error: {exc}]"
        fact_block = f"■ [1단계 검증 팩트 표]\n{fact_table}\n\n"

    # ── Stage 2: Synthesis ──
    synth_sys = (
        "당신은 국사편찬위원회 및 독립기념관 수준의 전문성을 갖춘 '3·1운동 역사 AI 도슨트'입니다.\n"
        "당신의 임무는 제공된 지식그래프(Graph DB) 사료와 RDB 문맥(Context)을 교차 분석하여, 사용자의 질의에 대한 학술적이고 객관적인 역사 해설을 제공하는 것입니다.\n\n"
        "답변을 작성할 때 다음의 원칙을 엄격하게 준수하십시오:\n\n"
        "1. [유동적 목차 생성]\n"
        "절대 고정된 목차(예: 서론-국외 항일 지도부-국내 시위-결론)를 기계적으로 사용하지 마십시오. 사용자의 질의 의도와 제공된 사료의 성격에 맞추어 가장 적합한 소제목 3~4개를 유동적으로 생성하여 논리적인 흐름을 구성하십시오.\n"
        "(예시: 사건의 배경 -> 전개 과정 -> 일제의 탄압 양상 -> 사료적 의의)\n\n"
        "2. [노이즈 데이터 자체 필터링]\n"
        "검색된 문맥 안에 질의와 직접적인 연관성이 없는 거물급 인물(예: 손병희, 이동휘 등)이나 무관한 지역 명칭이 단순 허브(Hub) 효과로 섞여 들어왔을 경우, 이를 해설에 억지로 끼워 맞추지 말고 과감히 배제하십시오. 오직 질문의 핵심이 되는 장소, 사건, 그리고 실제 주도한 기층 민중에 집중하십시오.\n\n"
        "3. [엄밀한 사료 비판과 환각 방지]\n"
        "제공된 사료에 나타난 지명 오기(예: 남리->제암리)나 겹치는 지명(예: 경기 장안면 vs 경남 장안면)이 있다면, 사료 비판적 관점에서 이를 명확히 구분하고 교정하여 서술하십시오. 제공된 문맥에 없는 내용은 절대 지어내지 마십시오.\n\n"
        "4. [답변의 완결성]\n"
        "문장이 중간에 끊기지 않도록 분량을 조절하십시오. 마지막 단락(결론)은 해당 사건이나 인물이 지니는 역사적 의의를 2~3문장으로 간결하고 명확하게 요약하여 완벽하게 끝맺음하십시오.\n\n"
        "5. [엄격 모드 출력]\n"
        "각 핵심 주장 뒤에 근거를 괄호로 표시하십시오. 괄호 안에는 <SOURCE_EVIDENCE id=\"...\">에 적힌 id(예: raw_bibliography-123) "
        "또는 <GRAPH_TOPOLOGY>에 그대로 적힌 사건명만 쓰십시오. 관계 이름(P70_documents, P106i 등), 블록 이름(GRAPH_TOPOLOGY, 블록 1), "
        "'Graph DB'나 '지식그래프' 같은 표기는 근거로 쓰지 마십시오. "
        "근거가 없는 질문의 전제는 부정하거나 '자료에서 확인되지 않음'이라고 답하십시오. "
        "검색 문맥에 없는 인물·날짜·장소·인과관계를 보완 지식으로 채우지 마십시오."
    )
    synth_user = f"[사용자 질의]: {query_item['query']}\n\n[검색된 지식그래프 및 사료 문맥]:\n{fact_block}■ [원문 사료 및 그래프 컨텍스트]\n{scoped_context}\n\n위 원칙과 문맥을 바탕으로 전문적인 도슨트 해설을 작성하십시오."
    if two_stage:
        time.sleep(2.0)
    try:
        answer = call_llm([
            {"role": "system", "content": synth_sys},
            {"role": "user", "content": synth_user}
        ])
    except Exception as exc:
        answer = f"[Synthesis Error: {exc}]"
    t_generation = time.time() - t_gen_start

    prompt_chars = len(synth_sys) + len(synth_user) + (len(ext_sys) + len(ext_user) if two_stage else 0)
    prompt_tokens = prompt_chars // 3
    output_tokens = len(answer) // 3

    return {
        "model": model_name,
        "answer": answer,
        "contexts": [scoped_context] if scoped_context else [],
        **({"fact_table": fact_table} if two_stage else {}),
        "context_chars": len(scoped_context),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "retrieval_time": round(t_retrieval, 3),
        "generation_time": round(t_generation, 3),
        "total_time": round(t_retrieval + t_generation, 3),
    }


def run_docent_1stage(query_item: dict[str, Any]) -> dict[str, Any]:
    """Ablation: Docent's evidence, answered with the synthesis prompt only (no fact-table call)."""
    return run_docent_hybrid_rag(query_item, "D-CENT controlled, 1-stage (Ablation)", two_stage=False)


def run_hybrid_2stage(query_item: dict[str, Any]) -> dict[str, Any]:
    """Ablation: the hybrid baseline's evidence, answered with Docent's fact table -> answer generation."""
    # Same search text, top_k and budget cut as run_hybrid_vector_rag; each chunk gets a citable id.
    search_text = query_item["query"]
    chunks = [chunk_block(c) for c in retrieve_chunks(search_text, top_k=candidate_count(500))]
    evidence = "\n\n".join(pack_context(chunks, "\n\n"))
    return run_docent_hybrid_rag(query_item, "Hybrid + 2-Stage (Ablation)", scoped_context=evidence)


# ─────────────────────────────────────────────────────────────────────────────
# D-CENT end to end: the deployed system over HTTP
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
_KNOWN_ENTITIES: list[str] | None = None
_CLIENT_STOP = {"무엇", "어떤", "누구", "알려줘", "설명해줘", "있습니까", "있는가", "관련"}


def client_keywords(question: str) -> list[str]:
    """Search keywords as static/docent.js derives them for a Korean question (classifyIntent)."""
    global _KNOWN_ENTITIES
    if _KNOWN_ENTITIES is None:
        js = (ROOT / "static" / "docent.js").read_text(encoding="utf-8")
        block = re.search(r"const KNOWN_ENTITIES = \[(.*?)\];", js, flags=re.DOTALL)
        _KNOWN_ENTITIES = re.findall(r"'([^']+)'", block.group(1)) if block else []
    q = question.strip()
    found = [e for e in _KNOWN_ENTITIES if e in q]
    tokens = [re.sub(r"(?:의|은|는|이|가|을|를|과|와|도|에서|에게|으로|로|에|란|이란)$", "", t).strip()
              for t in re.split(r"[\s,·.?!~]+", q)]
    tokens = [t for t in tokens if len(t) >= 2 and t not in _CLIENT_STOP]
    keywords = list(dict.fromkeys(found + tokens))[:5]
    return keywords or [q]


def _pg_text(row: dict[str, Any]) -> str:
    """Same string static/docent.js builds from a pg_prefetch row."""
    snippets = "; ".join(f"{k}: {v}" for k, v in (row.get("snippets") or {}).items() if v)
    return f"[{row.get('schema')}.{row.get('table')}] node={row.get('node_id')} rowid={row.get('rowid')} :: {snippets}"


def run_dcent_e2e(query_item: dict[str, Any], use_paths: bool = True) -> dict[str, Any]:
    """Deployed D-CENT pipeline: narrative (in parallel), analyze, graph, pg_prefetch, explain.

    Only the question text is sent. explain waits at most 20 s for the narrative request,
    as the web client does.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    global _DCENT
    if _DCENT is None:
        _DCENT = DcentClient(setting("DOCENT_URL", ""))
    client = _DCENT
    question = query_item["query"]
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        narrative_future = pool.submit(DcentClient(client.url).call, "narrative", {"term": question}, timeout=20.0) if use_paths else None
        analysis = client.call("analyze", {"term": question})
        keywords = client_keywords(question)
        graph = client.call("graph", {"term": question, "keywords": json.dumps(keywords, ensure_ascii=False),
                                      "intent_ko": question})
        names = graph.get("prefetch_names") or [n.get("raw_id") for n in graph.get("nodes", []) if n.get("raw_id")]
        refs = graph.get("record_refs") or []
        rows = client.call("pg_prefetch", {"names": json.dumps(names, ensure_ascii=False),
                                           "refs": json.dumps(refs, ensure_ascii=False)}) if (names or refs) else []
        narrative = None
        if narrative_future is not None:
            try:
                result = narrative_future.result(timeout=max(0.0, 20.0 - (time.time() - t0)) + 0.01)
                narrative = result if result and result.get("rows") else None
            except FutureTimeout:
                narrative = None  # the client proceeds without it; explain then computes the paths itself
            except Exception as exc:
                print(f" (narrative 생략: {exc})", end="", flush=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    t_retrieval = time.time() - t0

    data = {
        "term": question, "focus": (analysis or {}).get("focus", ""), "lang": "ko",
        "evidences": json.dumps(graph.get("evidences", []), ensure_ascii=False),
        "pg_texts": json.dumps([_pg_text(r) for r in rows], ensure_ascii=False),
        "eval": "1", "temperature": str(TEMPERATURE), "top_p": str(TOP_P), "max_tokens": str(ANSWER_MAX_TOKENS),
    }
    if narrative:
        data["narrative"] = json.dumps(narrative, ensure_ascii=False)
    if not use_paths:
        data["no_narrative"] = "1"
    t_gen = time.time()
    out = client.call("explain", data, timeout=300)
    if "context" not in out:
        raise RuntimeError("explain이 문맥을 반환하지 않았습니다: 서버에 DOCENT_EVAL_MODE=1이 설정되어 있는지 확인하세요")
    t_generation = time.time() - t_gen
    context = out.get("context", "")
    answer = out.get("text") or "[Error: empty answer]"
    fact_table = out.get("fact_table", "")
    return {
        "model": "D-CENT (end-to-end)" if use_paths else "D-CENT without narrative paths (Ablation)",
        "answer": answer,
        "contexts": out.get("context_items", [context] if context else []),
        # The server skips the fact table silently when that call fails; count it as a failed run.
        "fact_table": fact_table if (fact_table or not context) else "[Fact Extraction Error: empty]",
        "context_chars": len(context),
        "prompt_tokens": int(out.get("prompt_chars", 0)) // 3,
        "output_tokens": len(answer) // 3,
        "cypher_record_count": graph.get("cypher_record_count", 0),
        "narrative_used": bool(out.get("graphrag_used")),
        "retrieval_time": round(t_retrieval, 3),
        "generation_time": round(t_generation, 3),
        "total_time": round(t_retrieval + t_generation, 3),
    }


def run_dcent_no_paths(query_item: dict[str, Any]) -> dict[str, Any]:
    """Ablation: the deployed pipeline without narrative paths."""
    return run_dcent_e2e(query_item, use_paths=False)


# ─────────────────────────────────────────────────────────────────────────────


SYSTEMS.update({
    "passage": ("Passage baseline", run_vector_rag),
    "hybrid": ("Hybrid baseline", run_hybrid_vector_rag),
    "graph": ("Graph-only baseline", run_graph_rag),
    "dcent": ("D-CENT (end-to-end)", run_dcent_e2e),
    "controlled": ("D-CENT (controlled evidence)", run_docent_hybrid_rag),
    "no-paths": ("A: no narrative paths", run_dcent_no_paths),
    "one-stage": ("B: controlled, one stage", run_docent_1stage),
    "hybrid-two-stage": ("C: hybrid, two stages", run_hybrid_2stage),
})


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
