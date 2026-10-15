#!/usr/bin/env python3
"""Quantitative Evaluation: passage, hybrid and graph-only baselines vs. D-CENT.

Evaluates:
1. Passage baseline ("vector"): records whose TEI contains an entity string
2. Hybrid baseline: BM25 + dense, RRF fusion, cross-encoder rerank
3. Graph-only baseline: Neo4j triples without record text
4. Microsoft GraphRAG reference implementation (opt-in, needs a built index)
5. D-CENT end-to-end ("dcent"): the deployed system called over HTTP (docent.php) with the
   question text only: generated Cypher, full-text cross-check, narrative paths, record
   retrieval by document identifiers, two-stage generation. The server must run with
   DOCENT_EVAL_MODE=1 so that explain returns the context it generated from.
6. Ablation without narrative paths ("dcent_no_paths"): the same requests, explain told to
   leave the narrative paths out; the freed budget goes to record text.
7. D-CENT controlled evidence ("docent"): name-matched triples and bibliographic records
   assembled by this harness, answered with the two-stage generation.
8. Ablations of the generation procedure: docent_1stage (controlled D-CENT evidence, one call)
   and hybrid_2stage (hybrid-baseline evidence, fact table -> answer).

Metrics:
- Faithfulness / Fact Recall (%)
- Hallucination Rate (%) [Zero-correlation trap detection & cross-attribution error rate]
- Context Tokens & Output Tokens
- Information Density / Token Efficiency (Facts / 1k context tokens)
- Latency (seconds)

Outputs:
- table_rag_comparison.tex (IEEE/ACM formatted publication table)
- rag_evaluation_results.csv (Detailed per-query breakdown)
- rag_category_results.csv / rag_significance.csv / table_rag_by_category.tex
  (per-category results and paired significance tests, see analyze_rag_results.py)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.config import get_neo4j_driver, get_pg_connection, load_secrets, setting
from validate_benchmark import DATE_TOKEN_RE, is_generic_token, strip_josa
from verify_citations import clean_answer, verify_answer

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "multihop_benchmark_dataset.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# Conditions shared by every pipeline, so differences in the results come from
# the pipelines and not from their settings. All are set from the CLI in main().
GEN_TEMPERATURE = 0.0
GEN_TOP_P = 0.8
CONTEXT_CHARS = 11000         # retrieved-context budget per question (the deployed system's); 0 = legacy sizes
ANSWER_MAX_TOKENS = 1500      # output limit of every answer-generation call
RETRIEVAL_INPUT = "question"  # "question": entities extracted from the question / "entities": gold target_entities
DCENT_URL = ""                # docent.php of a server running with DOCENT_EVAL_MODE=1 (--dcent-url / DOCENT_URL)


def call_llm(messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
    """Call Google Gemini API using configured key."""
    temperature, top_p = GEN_TEMPERATURE, GEN_TOP_P
    max_tokens = max_tokens or ANSWER_MAX_TOKENS
    secrets = load_secrets()
    api_key = setting("GEMINI_API_KEY", "", secrets)
    model = setting("GEMINI_MODEL", "gemini-flash-lite-latest", secrets)
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

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
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif resp.status_code in (429, 503):
                wait_time = max(15, (attempt + 1) * 10)
                print(f" (재시도 대기 {wait_time}초: {resp.status_code})...", end="", flush=True)
                time.sleep(wait_time)
                continue
            else:
                raise RuntimeError(f"Gemini API 호출 실패 (상태 코드 {resp.status_code}): {resp.text[:200]}")
        except requests.RequestException as e:
            if attempt == 3:
                raise RuntimeError(f"Gemini API 요청 실패: {e}")
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


_ENTITY_CACHE: dict[str, list[str]] = {}


def question_entities(query: str) -> list[str]:
    """Extract search entities from the question text (one LLM call per question, shared by all pipelines)."""
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
        _ENTITY_CACHE[query] = [str(e).strip() for e in json.loads(match.group(0)) if str(e).strip()]
    return _ENTITY_CACHE[query]


def retrieval_entities(query_item: dict[str, Any]) -> list[str]:
    """Entities every pipeline may use for retrieval under the current input condition."""
    if RETRIEVAL_INPUT == "entities":
        return list(query_item["target_entities"])
    return question_entities(query_item["query"])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pipeline 1: Vanilla Vector RAG
# ─────────────────────────────────────────────────────────────────────────────
def retrieve_vector_chunks(query: str, target_entities: list[str], top_k: int = 5) -> list[str]:
    """Retrieve raw text chunks based on lexical/semantic matching from PG raw records."""
    conn = get_pg_connection()
    cur = conn.cursor()
    chunks = []
    for ent in target_entities[:3]:
        cur.execute("""
            SELECT tei FROM raw_bibliography WHERE tei LIKE %s LIMIT %s
        """, (f"%{ent}%", top_k))
        for r in cur.fetchall():
            clean = re.sub(r'<[^>]+>', ' ', r[0])
            clean = re.sub(r'\s+', ' ', clean).strip()
            if clean:
                chunks.append(clean[:400])
        if len(chunks) >= top_k:
            break
    cur.close()
    conn.close()
    return chunks[:top_k]


def run_vector_rag(query_item: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    chunks = retrieve_vector_chunks(query_item["query"], query_item["target_entities"], top_k=candidate_count(400))
    return _answer_from_chunks("Vector RAG", query_item, pack_context(chunks, CHUNK_SEPARATOR), time.time() - t0)


RERANKER = "cross-encoder"
CHUNK_SEPARATOR = "\n---\n"


def candidate_count(item_chars: int, legacy: int = 4) -> int:
    """Enough ranked items of about `item_chars` characters to fill the shared budget."""
    return CONTEXT_CHARS // item_chars + 1 if CONTEXT_CHARS > 0 else legacy


def run_hybrid_vector_rag(query_item: dict[str, Any]) -> dict[str, Any]:
    """BM25 + dense + rerank baseline. Retrieves from the question text only."""
    from baselines import hybrid_retrieve

    t0 = time.time()
    search_text = query_item["query"]
    if RETRIEVAL_INPUT == "entities":
        # Same information the other pipelines receive in this condition.
        search_text += " " + " ".join(query_item["target_entities"])
    chunks = [c["text"] for c in hybrid_retrieve(search_text, top_k=candidate_count(500), reranker=RERANKER)]
    return _answer_from_chunks("Hybrid Vector RAG (BM25+Dense+Rerank)", query_item, pack_context(chunks, CHUNK_SEPARATOR), time.time() - t0)


def _answer_from_chunks(model_name: str, query_item: dict[str, Any], chunks: list[str], t_retrieval: float) -> dict[str, Any]:
    context_str = CHUNK_SEPARATOR.join(chunks)
    system_prompt = (
        "You are a strict historical RAG assistant. Use only the retrieved document chunks. "
        "Separate documented facts from inference, never transfer a person or event across regions, "
        "and explicitly state '자료에서 확인되지 않음' when the question is not supported. "
        "Every factual claim must be traceable to a retrieved chunk."
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
# 2. Pipeline 2: Standard GraphRAG
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
        "do not merge similarly named places; explicitly state '자료에서 확인되지 않음' when unsupported."
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


def run_ms_graphrag(query_item: dict[str, Any]) -> dict[str, Any]:
    """Microsoft GraphRAG reference implementation, queried through its CLI.

    The CLI returns only the final answer, so contexts and prompt tokens are
    unavailable and the retrieval/token metrics are not comparable for it.
    """
    from baselines import msgraphrag_query

    t0 = time.time()
    try:
        answer = msgraphrag_query(query_item["query"])
    except Exception as exc:
        answer = f"[Error: {exc}]"
    elapsed = round(time.time() - t0, 3)
    return {
        "model": "Microsoft GraphRAG",
        "answer": answer,
        "contexts": [],
        "context_chars": 0,
        "prompt_tokens": 0,
        "output_tokens": len(answer) // 3,
        "retrieval_time": 0.0,
        "generation_time": elapsed,
        "total_time": elapsed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Pipeline 3: D-CENT with controlled evidence assembly
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
        envelopes.append(f"<GRAPH_TOPOLOGY>\n" + "\n".join(triples) + "\n</GRAPH_TOPOLOGY>")

    # 2. Scoped PostgreSQL primary sources (more candidates when a shared budget decides the cut-off)
    n_entities, per_entity = (3, max(4, CONTEXT_CHARS // 1500)) if CONTEXT_CHARS > 0 else (2, 2)
    for ent in target_entities[:n_entities]:
        cur.execute("""
            SELECT rowid, tei FROM raw_bibliography WHERE tei LIKE %s LIMIT %s
        """, (f"%{ent}%", per_entity))
        for rowid, tei in cur.fetchall():
            clean = re.sub(r'<[^>]+>', ' ', tei)
            clean = re.sub(r'\s+', ' ', clean).strip()[:500]
            envelopes.append(
                f'<SOURCE_EVIDENCE id="raw_bib_{rowid}" entity="{ent}">\n'
                f'  [사료 원문: {clean}]\n'
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
        "각 핵심 주장 뒤에 근거를 괄호로 표시하십시오. 괄호 안에는 <SOURCE_EVIDENCE id=\"...\">에 적힌 id(예: raw_bib_123) "
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
    from baselines import hybrid_retrieve

    # Same search text, top_k and budget cut as run_hybrid_vector_rag; each chunk gets a citable id.
    search_text = query_item["query"]
    if RETRIEVAL_INPUT == "entities":
        search_text += " " + " ".join(query_item["target_entities"])
    chunks = [c["text"] for c in hybrid_retrieve(search_text, top_k=candidate_count(500), reranker=RERANKER)]
    evidence = "\n\n".join(
        f'<SOURCE_EVIDENCE id="chunk_{n}">\n  [사료 원문: {chunk}]\n</SOURCE_EVIDENCE>'
        for n, chunk in enumerate(pack_context(chunks, CHUNK_SEPARATOR), 1)
    )
    return run_docent_hybrid_rag(query_item, "Hybrid + 2-Stage (Ablation)", scoped_context=evidence)


# ─────────────────────────────────────────────────────────────────────────────
# 4. D-CENT end-to-end: the deployed system over HTTP
# ─────────────────────────────────────────────────────────────────────────────
class DcentClient:
    """Calls docent.php the way static/docent.js does (CSRF token, form posts, retries on 403/429/5xx)."""

    def __init__(self, url: str):
        if not url:
            raise RuntimeError("D-CENT 서버 주소가 없습니다: --dcent-url 또는 DOCENT_URL을 설정하세요 (예: http://localhost:8000/docent.php)")
        self.url = url
        self.cookies: dict[str, str] = {}
        self.token = ""

    def _refresh_token(self) -> None:
        resp = requests.get(self.url, params={"csrf": "1"}, cookies=self.cookies, timeout=30)
        resp.raise_for_status()
        self.cookies.update(resp.cookies.get_dict())
        self.token = resp.json()["csrf_token"]

    def call(self, action: str, data: dict[str, Any], timeout: float = 180.0) -> Any:
        if not self.token:
            self._refresh_token()
        last = ""
        for _ in range(5):
            resp = requests.post(self.url, params={"ajax": action}, data={**data, "csrf_token": self.token},
                                 headers={"X-CSRF-Token": self.token}, cookies=self.cookies, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            last = f"{resp.status_code} {resp.text[:160]}"
            if resp.status_code == 403:
                self._refresh_token()
            elif resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "60") or 60)
                print(f" (D-CENT {action} 요청 제한, {wait}초 대기)...", end="", flush=True)
                time.sleep(wait)
            elif resp.status_code >= 500:
                time.sleep(10)
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

    Only the question text is sent; the retrieval-input condition does not apply. explain
    waits at most 20 s for the narrative request, as the web client does.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    global _DCENT
    if _DCENT is None:
        _DCENT = DcentClient(DCENT_URL)
    client = _DCENT
    question = query_item["query"]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=1) as pool:
        narrative_future = pool.submit(client.call, "narrative", {"term": question}) if use_paths else None
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
    t_retrieval = time.time() - t0

    data = {
        "term": question, "focus": (analysis or {}).get("focus", ""), "lang": "ko",
        "evidences": json.dumps(graph.get("evidences", []), ensure_ascii=False),
        "pg_texts": json.dumps([_pg_text(r) for r in rows], ensure_ascii=False),
        "eval": "1", "temperature": str(GEN_TEMPERATURE), "top_p": str(GEN_TOP_P), "max_tokens": str(ANSWER_MAX_TOKENS),
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
        "contexts": [context] if context else [],
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
# 5. Metric Evaluation Harness
# ─────────────────────────────────────────────────────────────────────────────
def is_generation_failure(answer: str) -> bool:
    """True when the pipeline produced no answer (API error), as opposed to a wrong one."""
    stripped = answer.strip()
    return not stripped or bool(re.match(r"\[[A-Za-z ]*Error", stripped))


def run_pipeline(query_item: dict[str, Any], model_name: str, runner_fn: Any, retries: int, delay: float) -> dict[str, Any]:
    """Run one pipeline on one question under the shared input condition, retrying failed generations."""
    res: dict[str, Any] = {}
    for attempt in range(retries + 1):
        if attempt:
            wait = max(delay, 30.0) * attempt
            print(f"    🔁 생성 실패, {wait:.0f}초 후 재시도 ({attempt}/{retries})...", flush=True)
            time.sleep(wait)
        try:
            run_item = {**query_item, "target_entities": retrieval_entities(query_item)}
            res = runner_fn(run_item)
        except Exception as exc:
            res = {"model": model_name, "answer": f"[Error: {exc}]", "contexts": [], "context_chars": 0,
                   "prompt_tokens": 0, "output_tokens": 0, "total_time": 0.0}
        # A failed fact-table stage means the Docent answer was not produced by the full pipeline.
        if not is_generation_failure(res["answer"]) and not is_generation_failure(res.get("fact_table", "ok")):
            res["failed"] = False
            return res
    res["failed"] = True
    return res


def evaluate_faithfulness(answer: str, gold_facts: list[str]) -> tuple[float, int, int]:
    """Check how many gold facts are covered accurately in the answer."""
    if not gold_facts:
        return 100.0, 0, 0
    covered = 0
    for fact in gold_facts:
        # Extract core keywords from fact
        keywords = [k for k in re.findall(r'[가-힣A-Za-z0-9]{2,}', fact) if k not in ['에서', '으로', '하고', '등이', '하여', '전개함', '받음', '형성됨']]
        hit_count = sum(1 for kw in keywords if kw in answer)
        if hit_count >= max(1, len(keywords) // 2):
            covered += 1
    score = (covered / len(gold_facts)) * 100.0
    return round(score, 2), covered, len(gold_facts)


def evaluate_retrieval_direct(query_item: dict[str, Any], contexts: list[str]) -> dict[str, float]:
    """Compute entity Hit Rate@K and MRR without an LLM judge."""
    targets = [str(value).strip().lower() for value in query_item.get("target_entities", []) if str(value).strip()]
    normalized = [context.lower() for context in contexts]
    if not targets:
        return {"entity_hit_rate_at_k": 0.0, "entity_mrr": 0.0}
    hits = sum(any(target in context for context in normalized) for target in targets)
    first_rank = next(
        (rank for rank, context in enumerate(normalized, 1)
         if any(target in context for target in targets)),
        0,
    )
    return {
        "entity_hit_rate_at_k": round(hits / len(targets), 4),
        "entity_mrr": round(1 / first_rank, 4) if first_rank else 0.0,
    }


def citation_stats(answer: str, contexts: list[str]) -> dict[str, int]:
    """Count record-id citations (valid / total), internal-label citations, and named sources found in the evidence."""
    cites = verify_answer(answer, contexts)
    return {
        "cite_ids_valid": sum(1 for c in cites if c["kind"] == "record_id" and c["valid"]),
        "cite_ids_total": sum(1 for c in cites if c["kind"] == "record_id"),
        "cite_internal_labels": sum(1 for c in cites if c["kind"] == "internal_label"),
        "cite_named_found": sum(1 for c in cites if c["kind"] == "named_source" and c["valid"]),
        "cite_named_total": sum(1 for c in cites if c["kind"] == "named_source"),
    }


def evaluate_hallucination(
    answer: str, negative_traps: list[str], category: str, entities: list[str] | None = None,
) -> tuple[float, int, str]:
    """Evaluate hallucination: Did the model falsely claim or endorse any negative traps?

    A trap counts when one answer sentence contains two of its keywords and the
    answer has no rejection phrase. One keyword is always a person name or
    proper noun (the trap's leading noun or one of the question's entities);
    years, dates and province/county/city names alone never match. Returns
    (rate, traps triggered, the sentences that triggered them for manual review).
    """
    if not negative_traps:
        return 0.0, 0, ""
    negation_markers = [
        '아니다', '없다', '않았다', '불가능', '확인되지', '오류', '차이', '별개의',
        '아닙니다', '없습니다', '않았습니다', '않습니다',
        '허구', '허위', '부합하지 않', '사실이 아니', '사실과 다르', '확인할 수 없', '근거가 없', '근거는 없', '근거 없',
    ]
    has_negation = any(neg in answer for neg in negation_markers)
    hints = [str(e).strip() for e in entities or [] if str(e).strip() and not is_generic_token(str(e).strip())]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", answer) if s.strip()]
    traps_triggered = 0
    flagged: list[str] = []

    for trap in negative_traps:
        tokens = [strip_josa(k) for k in re.findall(r'[가-힣A-Za-z0-9]{2,}', trap) if k not in ['에서', '으로', '하고', '직접', '했다', '시작했다']]
        tokens = list(dict.fromkeys(t for t in tokens if not DATE_TOKEN_RE.fullmatch(t)))
        anchors = [t for i, t in enumerate(tokens)
                   if not is_generic_token(t) and (i == 0 or any(h in t or h.startswith(t) for h in hints))]
        if not anchors or has_negation:
            continue  # a trap without a name or proper noun cannot be matched reliably
        # Prefer a second name/proper noun over an ordinary word as the other keyword.
        trap_keywords = [anchors[0]] + (anchors[1:] + [t for t in tokens if t not in anchors])[:1]
        hits = [s for s in sentences if all(kw in s for kw in trap_keywords)]
        if hits:
            traps_triggered += 1
            flagged += [s for s in hits if s not in flagged]

    rate = (traps_triggered / len(negative_traps)) * 100.0
    return round(rate, 2), traps_triggered, " ‖ ".join(flagged)


def export_rag_latex_table(results_by_model: dict[str, dict[str, Any]], out_path: Path) -> str:
    """Generate IEEE/ACM ready LaTeX table comparing the evaluated RAG models."""
    names = list(results_by_model)
    # (label, key, format, better direction: 1 higher / -1 lower / 0 none)
    rows = [
        (r"Fact Recall / Faithfulness (\%) $\uparrow$", "faithfulness", "{:.1f}\\%", 1),
        (r"Hallucination Rate (Traps) (\%) $\downarrow$", "hallucination_rate", "{:.1f}\\%", -1),
        ("Avg. Context Tokens Ingested", "avg_context_tokens", "{:.0f}", 0),
        ("Avg. Output Tokens Generated", "avg_output_tokens", "{:.0f}", 0),
        (r"Token Efficiency (Facts / 1k tokens) $\uparrow$", "token_efficiency", "{:.2f}", 1),
        (r"Avg. Latency (Total s) $\downarrow$", "avg_latency", "{:.2f}s", -1),
    ]
    token_keys = {"avg_context_tokens", "token_efficiency"}
    lines = [
        r"\begin{table*}[htbp]",
        r"\centering",
        r"\caption{Quantitative Comparative Evaluation of RAG Pipelines}",
        r"\label{tab:rag_comparison}",
        r"\begin{tabular}{l|" + "|".join("c" * len(names)) + "}",
        r"\hline",
        r"\textbf{Evaluation Metric} & " + " & ".join(rf"\textbf{{{n}}}" for n in names) + r" \\",
        r"\hline",
    ]
    for label, key, fmt, direction in rows:
        # Models that do not expose their prompt (context tokens == 0) get n/a.
        usable = {n: results_by_model[n][key] for n in names
                  if not (key in token_keys and results_by_model[n]["avg_context_tokens"] == 0)}
        best = None
        if direction and usable:
            best = max(usable.values()) if direction > 0 else min(usable.values())
            if list(usable.values()).count(best) > 1:
                best = None  # ties are not highlighted
        cells = []
        for n in names:
            if n not in usable:
                cells.append("n/a")
                continue
            text = fmt.format(usable[n])
            cells.append(rf"\textbf{{{text}}}" if best is not None and usable[n] == best else text)
        lines.append(f"{label} & " + " & ".join(cells) + r" \\")
    lines += [r"\hline", r"\end{tabular}", r"\end{table*}", ""]
    latex = "\n".join(lines)
    out_path.write_text(latex, encoding="utf-8")
    return latex


def export_rag_csv(detailed_records: list[dict[str, Any]], out_path: Path) -> None:
    if not detailed_records:
        return
    keys = list(detailed_records[0].keys())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(detailed_records)


MODEL_REGISTRY = {
    "vector": ("Vector RAG", run_vector_rag),
    "hybrid": ("Hybrid Vector RAG (BM25+Dense+Rerank)", run_hybrid_vector_rag),
    "graph": ("Standard GraphRAG", run_graph_rag),
    "msgraphrag": ("Microsoft GraphRAG", run_ms_graphrag),
    "dcent": ("D-CENT (end-to-end)", run_dcent_e2e),
    "dcent_no_paths": ("D-CENT without narrative paths (Ablation)", run_dcent_no_paths),
    "docent": ("D-CENT (controlled evidence)", run_docent_hybrid_rag),
    "docent_1stage": ("D-CENT controlled, 1-stage (Ablation)", run_docent_1stage),
    "hybrid_2stage": ("Hybrid + 2-Stage (Ablation)", run_hybrid_2stage),
}
DEFAULT_MODELS = "vector,hybrid,graph,dcent,dcent_no_paths,docent,docent_1stage,hybrid_2stage"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate passage, hybrid and graph-only baselines against D-CENT.")
    parser.add_argument("--quick", action="store_true", help="Run on 3 representative queries (1 Multi-hop, 1 Spatial, 1 Trap)")
    parser.add_argument("--single", action="store_true", help="Run single query (Q_MH_01) and display full response_text for each model")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of queries to evaluate")
    parser.add_argument("--delay", type=float, default=15.0, help="Delay in seconds between API calls to prevent 429 rate limit (default: 15.0)")
    parser.add_argument("--ragas", action="store_true", help="Run RAGAS metrics after generating benchmark responses")
    parser.add_argument("--models", default=DEFAULT_MODELS,
                        help=f"Comma-separated pipelines to run. Choices: {','.join(MODEL_REGISTRY)} (default: {DEFAULT_MODELS})")
    parser.add_argument("--reranker", choices=["cross-encoder", "none"], default="cross-encoder",
                        help="Reranker for the hybrid baseline (default: cross-encoder)")
    parser.add_argument("--retrieval-input", choices=["question", "entities"], default="question",
                        help="What every pipeline may retrieve with: entities extracted from the question text (default), "
                             "or the benchmark's gold target_entities")
    parser.add_argument("--temperature", type=float, default=0.0, help="Generation temperature for every LLM call (default: 0.0)")
    parser.add_argument("--top-p", type=float, default=0.8, help="Generation top_p for every LLM call (default: 0.8)")
    parser.add_argument("--context-chars", type=int, default=11000,
                        help="Retrieved-context character budget shared by all pipelines (default: 11000, the deployed system's; "
                             "0 = legacy per-pipeline sizes). The deployed system reads its budget from DOCENT_CONTEXT_CHARS on the server")
    parser.add_argument("--max-output-tokens", type=int, default=1500, help="Output limit of every answer call (default: 1500)")
    parser.add_argument("--dcent-url", default=setting("DOCENT_URL", ""),
                        help="docent.php of a D-CENT server started with DOCENT_EVAL_MODE=1 (default: DOCENT_URL)")
    parser.add_argument("--retries", type=int, default=2, help="Extra attempts for a failed generation (default: 2)")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH,
                        help="Benchmark JSON to evaluate (default: dataset/multihop_benchmark_dataset.json)")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR,
                        help="Directory for all result files, so runs can be kept apart (default: evaluation/results)")
    args = parser.parse_args()

    global RERANKER, RETRIEVAL_INPUT, GEN_TEMPERATURE, GEN_TOP_P, CONTEXT_CHARS, ANSWER_MAX_TOKENS, DCENT_URL
    RERANKER = args.reranker
    RETRIEVAL_INPUT = args.retrieval_input
    GEN_TEMPERATURE, GEN_TOP_P, CONTEXT_CHARS = args.temperature, args.top_p, args.context_chars
    ANSWER_MAX_TOKENS, DCENT_URL = args.max_output_tokens, args.dcent_url
    model_keys = [k.strip() for k in args.models.split(",") if k.strip()]
    unknown = [k for k in model_keys if k not in MODEL_REGISTRY]
    if unknown:
        parser.error(f"알 수 없는 모델: {unknown} (선택 가능: {list(MODEL_REGISTRY)})")

    if not args.dataset.is_file():
        parser.error(f"데이터셋 파일이 없습니다: {args.dataset}")
    benchmark_data = json.loads(args.dataset.read_text(encoding="utf-8"))
    dataset_name = args.dataset.name
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.single:
        selected = [benchmark_data[0]]
    elif args.quick:
        # Select 1 multi-hop, 1 spatial diffusion, and 1 zero-correlation trap
        selected = [
            benchmark_data[0],  # Q_MH_01: Boseongsa -> Pyongyang Sudok School
            benchmark_data[4],  # Q_ST_01: Seoul -> Pyongyang -> Hamhung diffusion
            benchmark_data[9],  # Q_ZC_01: Lee Dong-hwi vs Aunae market trap
        ] if len(benchmark_data) > 9 else benchmark_data[:3]
    elif args.limit > 0:
        selected = benchmark_data[:args.limit]
    else:
        selected = benchmark_data

    mode_str = "1번 질의 단독 디버깅 모드" if args.single else f"총 {len(selected)}건 정량 평가 모드"
    print(f"🚀 [RAG 정량 평가 벤치마크] {mode_str}를 시작합니다. (호출 간 딜레이: {args.delay}초)")
    print(f"   데이터셋: {args.dataset} / 결과 폴더: {out_dir}")
    print(f"   공통 조건: 검색 입력={RETRIEVAL_INPUT}, temperature={GEN_TEMPERATURE}, top_p={GEN_TOP_P}, "
          f"문맥 예산={CONTEXT_CHARS or '제한 없음'}자, 재시도={args.retries}회")

    models = [MODEL_REGISTRY[k] for k in model_keys]
    if any(k.startswith("dcent") for k in model_keys):
        if not DCENT_URL:
            parser.error("dcent 조건에는 --dcent-url 또는 DOCENT_URL이 필요합니다 (DOCENT_EVAL_MODE=1로 실행 중인 docent.php)")
        if CONTEXT_CHARS not in (0, 11000):
            print(f"   ⚠️ 배포 시스템의 문맥 예산은 서버의 DOCENT_CONTEXT_CHARS(기본 11000)로 정해집니다: 서버도 {CONTEXT_CHARS}로 맞추세요")
        if RETRIEVAL_INPUT == "entities":
            print("   ⚠️ dcent 조건은 질문 원문만 받으므로 --retrieval-input entities가 적용되지 않습니다")

    detailed_records = []
    ragas_records = []
    failed_generations = 0
    aggregated = {m_name: {
        "faithfulness_scores": [],
        "hallucination_scores": [],
        "context_tokens": [],
        "output_tokens": [],
        "latencies": [],
        "total_facts_covered": 0,
        "total_facts": 0,
    } for m_name, _ in models}

    for i, item in enumerate(selected, 1):
        print(f"\n[{i}/{len(selected)}] [{item['category']}] {item['query']}")
        for m_name, runner_fn in models:
            print(f"\n  🔹 [{m_name}] 실행 중...", flush=True)
            res = run_pipeline(item, m_name, runner_fn, args.retries, args.delay)
            failed = res["failed"]
            if failed:
                failed_generations += 1
                print(f"    ⚠️ 생성 실패 — 이 질문은 유의성 검정에서 제외됩니다: {res['answer'][:120]}")
            f_score, f_cov, f_tot = evaluate_faithfulness(res["answer"], item["gold_facts"])
            h_score, h_traps, trap_flagged = evaluate_hallucination(res["answer"], item["negative_traps"], item["category"], item.get("target_entities"))

            aggregated[m_name]["faithfulness_scores"].append(f_score)
            aggregated[m_name]["hallucination_scores"].append(h_score)
            aggregated[m_name]["context_tokens"].append(res["prompt_tokens"])
            aggregated[m_name]["output_tokens"].append(res["output_tokens"])
            aggregated[m_name]["latencies"].append(res["total_time"])
            aggregated[m_name]["total_facts_covered"] += f_cov
            aggregated[m_name]["total_facts"] += f_tot

            if args.single:
                if "fact_table" in res and res["fact_table"]:
                    print(f"    📊 [Stage 1 검증 팩트 표]:\n{res['fact_table']}")
                print(f"    📄 [생성된 response_text]:\n{res['answer']}")

            detailed_records.append({
                "query_id": item["id"],
                "category": item["category"],
                "source": item.get("source", ""),
                "dataset": dataset_name,
                "model": m_name,
                "faithfulness": f_score,
                "hallucination_rate": h_score,
                "prompt_tokens": res["prompt_tokens"],
                "context_chars": res.get("context_chars", 0),
                "output_tokens": res["output_tokens"],
                "latency_sec": res["total_time"],
                "answer_snippet": res["answer"].replace("\n", " ")[:120],
                "cypher_record_count": res.get("cypher_record_count", ""),
                "narrative_used": res.get("narrative_used", ""),
                "generation_failed": failed,
                "trap_flagged_text": trap_flagged,
                **evaluate_retrieval_direct(item, res.get("contexts", [])),
                **citation_stats(res["answer"], res.get("contexts", [])),
            })
            ragas_records.append({
                "user_input": item["query"],
                "response": res["answer"],
                "response_cleaned": clean_answer(res["answer"], res.get("contexts", [])),
                "retrieved_contexts": res.get("contexts", []),
                "reference": "\n".join(item.get("ground_truth", item["gold_facts"])),
                "model": m_name,
                "query_id": item["id"],
                "evaluation_type": item.get("evaluation_type", "direct"),
                "category": item["category"],
                "source": item.get("source", ""),
                "dataset": dataset_name,
                "generation_failed": failed,
            })
            print(f"    ✅ 완료 (Fact Recall: {f_score}%, Hallucination: {h_score}%, Latency: {res['total_time']}s)")
            if args.delay > 0:
                print(f"    ⏳ Rate-limit 방지 {args.delay}초 대기 중...", end="", flush=True)
                time.sleep(args.delay)
                print(" 완료.")

    # Aggregate summaries
    summary_by_model = {}
    for m_name, data in aggregated.items():
        avg_f = sum(data["faithfulness_scores"]) / len(data["faithfulness_scores"]) if data["faithfulness_scores"] else 0.0
        avg_h = sum(data["hallucination_scores"]) / len(data["hallucination_scores"]) if data["hallucination_scores"] else 0.0
        avg_ctx = sum(data["context_tokens"]) / len(data["context_tokens"]) if data["context_tokens"] else 0.0
        avg_out = sum(data["output_tokens"]) / len(data["output_tokens"]) if data["output_tokens"] else 0.0
        avg_lat = sum(data["latencies"]) / len(data["latencies"]) if data["latencies"] else 0.0
        tot_k_tokens = (sum(data["context_tokens"]) / 1000.0) if sum(data["context_tokens"]) > 0 else 1.0
        eff = data["total_facts_covered"] / tot_k_tokens

        summary_by_model[m_name] = {
            "faithfulness": round(avg_f, 2),
            "hallucination_rate": round(avg_h, 2),
            "avg_context_tokens": round(avg_ctx, 1),
            "avg_output_tokens": round(avg_out, 1),
            "token_efficiency": round(eff, 2),
            "avg_latency": round(avg_lat, 2),
        }

    # Console display
    print("\n" + "=" * 88)
    print(f"{'Model':<30} | {'Fact Recall(%)':<14} | {'Hallucination(%)':<16} | {'Tokens(In/Out)':<15} | {'Latency(s)':<10}")
    print("-" * 88)
    for m_name, stats in summary_by_model.items():
        tok_str = f"{int(stats['avg_context_tokens'])}/{int(stats['avg_output_tokens'])}"
        print(f"{m_name:<30} | {stats['faithfulness']:<14.1f} | {stats['hallucination_rate']:<16.1f} | {tok_str:<15} | {stats['avg_latency']:<10.2f}")
    print("=" * 88)

    # Export
    tex_path = out_dir / "table_rag_comparison.tex"
    csv_path = out_dir / "rag_evaluation_results.csv"
    export_rag_latex_table(summary_by_model, tex_path)
    export_rag_csv(detailed_records, csv_path)
    ragas_input_path = out_dir / "ragas_dataset.json"
    ragas_input_path.write_text(
        json.dumps(ragas_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n📄 LaTeX 표 생성 완료: {tex_path}")
    print(f"💾 CSV 상세 결과 저장 완료: {csv_path}")
    print(f"🧾 RAGAS 입력 데이터 저장 완료: {ragas_input_path}")

    from analyze_rag_results import run as run_analysis

    counts = run_analysis(csv_path, out_dir)
    print(f"📈 범주별 결과·쌍대 유의성 검정 저장 완료 (분석 대상 {counts['questions_used']}/{counts['questions_total']}문항): "
          f"{out_dir / 'rag_category_results.csv'}, {out_dir / 'rag_significance.csv'}")
    if counts["has_source"]:
        print(f"📈 출처별(source) 결과·유의성 검정 저장 완료: {out_dir / 'rag_source_results.csv'}, {out_dir / 'rag_source_significance.csv'}")
    if failed_generations:
        print(f"⚠️ 생성 실패 {failed_generations}건 발생: 위 요약 표의 평균에는 실패 응답이 포함되어 있습니다. "
              "--delay를 늘려 다시 실행하세요.")
    if args.ragas:
        from eval_ragas import evaluate_dataset

        ragas_output_path = out_dir / "ragas_results.json"
        evaluate_dataset(ragas_input_path, ragas_output_path)
        print(f"📊 RAGAS 결과 저장 완료: {ragas_output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
