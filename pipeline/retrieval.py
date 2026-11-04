"""Hybrid record retrieval shared by D-CENT and the hybrid baseline.

BM25 over words and character bigrams, plus dense retrieval with Ollama embeddings, fused by
reciprocal rank (RRF) and reranked with a cross-encoder. The pool is the record text of the three
corpus tables that D-CENT cites (register entries, source entries, bibliographic records), cut into
overlapping chunks. The dense index is cached on disk and rebuilt when the chunks change.

D-CENT's server reaches it through the local service in pipeline/retriever_server.py; the evaluation's
hybrid baseline calls ``hybrid_retrieve`` directly, so both read records with the same retriever.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from pipeline.config import ROOT, get_pg_connection, setting

CACHE_DIR = ROOT / "vector_store" / "baselines"   # dense index, built on first use
# The record pool of every system that reads record text: the records the end-to-end system reaches
# through its document nodes. Searching bibliographic records alone left the baselines without the
# register entries and source entries that name the participants of an incident.
CORPUS_TABLES = ("raw_event_info", "raw_source_info", "raw_bibliography")
ALLOWED_TABLES = {"raw_bibliography", "raw_source_info", "raw_event_info"}
EMBED_MODEL = "nomic-embed-text"                   # dense retriever (Ollama, OLLAMA_HOST)
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"           # cross-encoder
CHUNK_CHARS = 500
CHUNK_OVERLAP = 100
RRF_K = 60
CANDIDATES = 50


def corpus_tables() -> list[str]:
    """Tables searched by the systems that read record text (the same pool D-CENT cites)."""
    names = list(CORPUS_TABLES)
    unknown = set(names) - ALLOWED_TABLES
    if unknown:
        raise ValueError(f"지원하지 않는 코퍼스 테이블: {sorted(unknown)}")
    return names


def strip_tei(tei: str) -> str:
    """Record body as plain text; the teiHeader is generated boilerplate."""
    body = re.search(r"<body>(.*?)</body>", tei, flags=re.DOTALL)
    tei = body.group(1) if body else tei
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tei)).strip()


def load_documents(tables: list[str] | None = None) -> list[dict[str, Any]]:
    """Load TEI records as plain-text documents: {id, table, rowid, text}."""
    conn = get_pg_connection()
    cur = conn.cursor()
    docs = []
    for table in tables or corpus_tables():
        cur.execute(f'SELECT rowid, tei FROM "{table}" WHERE tei IS NOT NULL ORDER BY rowid')
        for rowid, tei in cur.fetchall():
            text = strip_tei(tei)
            if text:
                docs.append({"id": f"{table}:{rowid}", "table": table, "rowid": rowid, "text": text})
    cur.close()
    conn.close()
    return docs


def chunk_documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlapping chunks with ids "table:rowid#n"."""
    chunks = []
    step = CHUNK_CHARS - CHUNK_OVERLAP
    for doc in docs:
        text = doc["text"]
        for n, start in enumerate(range(0, max(len(text) - CHUNK_OVERLAP, 1), step)):
            chunks.append({"id": f"{doc['id']}#{n}", "text": text[start:start + CHUNK_CHARS]})
    return chunks


def chunk_record(chunk_id: str) -> tuple[str, str]:
    """"raw_bibliography:123#0" -> ("raw_bibliography", "123")."""
    table, rowid = chunk_id.split("#")[0].split(":")
    return table, rowid


# ── BM25 ─────────────────────────────────────────────────────────────────────
def tokenize(text: str) -> list[str]:
    """Words plus character bigrams, so Korean matches without a morph analyzer."""
    tokens = []
    for word in re.findall(r"[가-힣一-鿿A-Za-z0-9]+", text.lower()):
        tokens.append(word)
        if len(word) > 2:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


class BM25Index:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n_docs = len(texts)
        self.doc_len = np.zeros(self.n_docs, dtype=np.float32)
        postings: dict[str, tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))
        for doc_id, text in enumerate(texts):
            counts = Counter(tokenize(text))
            self.doc_len[doc_id] = sum(counts.values())
            for token, tf in counts.items():
                ids, tfs = postings[token]
                ids.append(doc_id)
                tfs.append(tf)
        self.avg_len = float(self.doc_len.mean()) if self.n_docs else 0.0
        self.postings = {
            token: (np.asarray(ids, dtype=np.int32), np.asarray(tfs, dtype=np.float32))
            for token, (ids, tfs) in postings.items()
        }

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(self.n_docs, dtype=np.float32)
        for token in set(tokenize(query)):
            if token not in self.postings:
                continue
            ids, tfs = self.postings[token]
            idf = math.log(1 + (self.n_docs - len(ids) + 0.5) / (len(ids) + 0.5))
            norm = self.k1 * (1 - self.b + self.b * self.doc_len[ids] / self.avg_len)
            out[ids] += idf * tfs * (self.k1 + 1) / (tfs + norm)
        return out


# ── Dense retrieval (Ollama embeddings, cached on disk) ──────────────────────
def _ollama_embed(texts: list[str], model: str, base_url: str, batch_size: int = 32) -> np.ndarray:
    """Embed texts with Ollama's /api/embed endpoint and L2-normalize the rows."""
    import requests

    endpoint = f"{base_url.rstrip('/')}/api/embed"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = [str(t or "")[:3000] for t in texts[start:start + batch_size]]
        for attempt in range(5):
            try:
                response = requests.post(endpoint, json={"model": model, "input": batch}, timeout=300)
                response.raise_for_status()
                embeddings = response.json().get("embeddings")
                if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                    raise RuntimeError(f"unexpected embedding count at offset {start}")
                vectors.extend(embeddings)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))
    matrix = np.asarray(vectors, dtype=np.float32)
    return matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)


def embed(texts: list[str], kind: str) -> np.ndarray:
    if "nomic" in EMBED_MODEL:  # nomic models are trained with task prefixes
        texts = [f"search_{kind}: {t}" for t in texts]
    return _ollama_embed(texts, EMBED_MODEL, setting("OLLAMA_HOST", "http://localhost:11434"))


def _chunks_digest(chunks: list[dict[str, Any]]) -> str:
    digest = hashlib.sha1()
    for chunk in chunks:
        digest.update(chunk["id"].encode())
        digest.update(chunk["text"].encode())
    return digest.hexdigest()[:12]


def _dense_matrix(chunks: list[dict[str, Any]], digest: str) -> np.ndarray:
    cache = CACHE_DIR / f"dense_{re.sub(r'[^A-Za-z0-9]+', '_', EMBED_MODEL)}_{digest}.npy"
    if cache.exists():
        return np.load(cache)
    print(f"  🧮 dense 임베딩 생성 중 ({len(chunks)} chunks, model={EMBED_MODEL}) — 최초 1회만 수행됩니다...", flush=True)
    matrix = embed([c["text"] for c in chunks], "document")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(cache, matrix)
    return matrix


# ── Index state and search ───────────────────────────────────────────────────
_STATE: dict[str, Any] = {}
_LOCK = threading.Lock()  # the cross-encoder and the lazy index build are not safe to share across threads


def ensure_index() -> dict[str, Any]:
    """Build (once) and describe the index: chunk count, models, and a digest of the chunks."""
    with _LOCK:
        if "chunks" not in _STATE:
            print("  📚 하이브리드 검색 인덱스 구축 중...", flush=True)
            chunks = chunk_documents(load_documents())
            digest = _chunks_digest(chunks)
            _STATE.update(chunks=chunks, digest=digest, bm25=BM25Index([c["text"] for c in chunks]),
                          dense=_dense_matrix(chunks, digest))
    return {"chunks": len(_STATE["chunks"]), "digest": _STATE["digest"], "embed_model": EMBED_MODEL,
            "rerank_model": RERANK_MODEL, "tables": list(CORPUS_TABLES)}


def _rerank(query: str, candidates: list[dict[str, Any]], reranker: str) -> list[dict[str, Any]]:
    if reranker == "none":
        return candidates
    if reranker != "cross-encoder":
        raise ValueError(f"알 수 없는 reranker: {reranker}")
    if "cross_encoder" not in _STATE:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError("재순위화에는 'sentence-transformers'가 필요합니다. requirements.txt를 설치하세요.") from exc
        # CPU by default: on Apple silicon the MPS pool is shared with other processes (Ollama) and a
        # long run ran out of it; RERANK_DEVICE=mps or cuda selects an accelerator explicitly.
        _STATE["cross_encoder"] = CrossEncoder(RERANK_MODEL, device=setting("RERANK_DEVICE", "cpu"))
    scores = _STATE["cross_encoder"].predict([(query, c["text"]) for c in candidates])
    order = np.argsort(-np.asarray(scores))
    return [candidates[i] for i in order]


def hybrid_retrieve(query: str, top_k: int = 4, candidates: int = CANDIDATES, reranker: str = "cross-encoder") -> list[dict[str, Any]]:
    """BM25 + dense -> RRF -> rerank. Returns chunks as {id, text}, best first."""
    ensure_index()
    with _LOCK:
        chunks = _STATE["chunks"]
        bm25_rank = np.argsort(-_STATE["bm25"].scores(query))[:candidates]
        dense_rank = np.argsort(-(_STATE["dense"] @ embed([query], "query")[0]))[:candidates]
        fused: dict[int, float] = defaultdict(float)
        for ranking in (bm25_rank, dense_rank):
            for rank, idx in enumerate(ranking, 1):
                fused[int(idx)] += 1.0 / (RRF_K + rank)
        pool = [chunks[i] for i, _ in sorted(fused.items(), key=lambda kv: -kv[1])[:candidates]]
        return _rerank(query, pool, reranker)[:top_k]
