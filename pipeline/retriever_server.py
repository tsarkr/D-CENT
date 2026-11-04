"""Local record-retrieval service for the D-CENT server.

  python -m pipeline retriever [--host 127.0.0.1] [--port 8765]

The PHP server (docent.php) cannot run the hybrid retriever itself, so it asks this process:

  GET  /health                       -> {"ok": true, "index": {...}}
  POST /search {"query": "...", "top_k": 20}
       -> {"results": [{"table", "rowid", "chunk", "text"}, ...], "index": {...}}

The index is built (or read from the dense cache) before the service starts listening, so the first
request does not time out. It listens on the loopback address only; set DOCENT_RETRIEVER_URL
(for example http://127.0.0.1:8765) for docent.php to use it.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pipeline import retrieval

MAX_TOP_K = 50
MAX_QUERY_CHARS = 2000


def search(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a /search request and return the ranked chunks with their records."""
    query = str(payload.get("query") or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        raise ValueError("query is required")
    try:
        top_k = int(payload.get("top_k", 20))
    except (TypeError, ValueError):
        raise ValueError("top_k must be an integer") from None
    top_k = max(1, min(MAX_TOP_K, top_k))
    results = []
    for chunk in retrieval.hybrid_retrieve(query, top_k=top_k):
        table, rowid = retrieval.chunk_record(chunk["id"])
        results.append({"table": table, "rowid": rowid, "chunk": chunk["id"], "text": chunk["text"]})
    return {"results": results, "index": retrieval.ensure_index()}


class Handler(BaseHTTPRequestHandler):
    server_version = "DocentRetriever/1"

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "index": retrieval.ensure_index()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/search":
            self._send(404, {"error": "not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON object expected")
            self._send(200, search(payload))
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # report, do not crash the service
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # one line per request would flood the terminal during an evaluation


def serve(host: str = "127.0.0.1", port: int = 8765) -> int:
    info = retrieval.ensure_index()
    print(f"🔎 검색 서비스: http://{host}:{port}  (chunks {info['chunks']}, digest {info['digest']})", flush=True)
    # Warm the cross-encoder so the first request is not slower than the rest.
    retrieval.hybrid_retrieve("3·1운동", top_k=1)
    print("   준비 완료. 종료하려면 Ctrl+C", flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
