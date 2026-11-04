<?php
/**
 * includes/retriever.php — 원천 기록 검색 (로컬 검색 서비스 호출).
 *
 * 기록 원문은 평가의 하이브리드 기준 시스템과 같은 검색기(BM25 + 의미 검색 + 재순위화,
 * pipeline/retrieval.py)로 찾는다. 검색기는 `python -m pipeline retriever`로 띄운 로컬 서비스이며
 * DOCENT_RETRIEVER_URL(예: http://127.0.0.1:8765)로 가리킨다.
 *
 * 설정이 없거나 서비스가 응답하지 않으면, 서비스에서는 이름 일치 검색만으로 계속하고(기록에 남김)
 * 평가 모드에서는 예외를 던진다: 평가가 검색기 없이 조용히 다른 조건으로 돌지 않도록.
 */

require_once __DIR__ . '/security.php';

function docent_retriever_url(): string {
    return rtrim(trim((string)get_cfg('DOCENT_RETRIEVER_URL', '')), '/');
}

/** 검색 서비스에 JSON 요청. 실패하면 RuntimeException. */
function _docent_retriever_request(string $path, ?array $payload, int $timeout): array {
    $url = docent_retriever_url();
    if ($url === '') throw new RuntimeException('DOCENT_RETRIEVER_URL is not set.');
    $ch = curl_init($url . $path);
    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_TIMEOUT => $timeout,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
    ];
    if ($payload !== null) {
        $options[CURLOPT_POST] = true;
        $options[CURLOPT_POSTFIELDS] = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_IGNORE);
    }
    curl_setopt_array($ch, $options);
    $res = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $errno = curl_errno($ch);
    $data = is_string($res) ? json_decode($res, true) : null;
    if ($res === false || $errno !== 0 || $code !== 200 || !is_array($data)) {
        $detail = is_array($data) ? (string)($data['error'] ?? '') : '';
        throw new RuntimeException(sprintf('Retriever %s failed: HTTP %d, cURL %d %s', $path, $code, $errno, $detail));
    }
    return $data;
}

/** 검색 서비스의 색인 정보(청크 수, 다이제스트, 모델). 설정이 없으면 null. */
function docent_retriever_index(): ?array {
    if (docent_retriever_url() === '') return null;
    $data = _docent_retriever_request('/health', null, 10);
    return is_array($data['index'] ?? null) ? $data['index'] : null;
}

/**
 * 질문으로 기록 청크를 찾아, 근거 조립(build_budgeted_pg_context)이 읽는 한 줄 형식으로 돌려준다:
 * "[public.<table>] node=기록검색 rowid=<rowid> :: <text>". 순위가 높은 것부터.
 */
function docent_retrieve_records(string $query, bool $required): array {
    if (docent_retriever_url() === '') {
        if ($required) throw new RuntimeException('DOCENT_RETRIEVER_URL is not set: the evaluation needs the record retriever.');
        return [];
    }
    try {
        $top_k = max(1, min(50, (int)get_cfg('DOCENT_RETRIEVER_TOP_K', '20')));
        $data = _docent_retriever_request('/search', ['query' => $query, 'top_k' => $top_k], 60);
    } catch (\Throwable $e) {
        if ($required) throw $e;
        error_log('[Docent] record retriever unavailable: ' . $e->getMessage());
        return [];
    }
    $lines = [];
    $seen = [];
    foreach (($data['results'] ?? []) as $r) {
        $table = (string)($r['table'] ?? '');
        $rowid = (string)($r['rowid'] ?? '');
        $text = trim(preg_replace('/\s+/u', ' ', (string)($r['text'] ?? '')));
        if (!preg_match('/^raw_[a-z_]+$/', $table) || !preg_match('/^\d+$/', $rowid) || $text === '') continue;
        $key = "{$table}-{$rowid}|{$text}";
        if (isset($seen[$key])) continue;
        $seen[$key] = true;
        $lines[] = "[public.{$table}] node=기록검색 rowid={$rowid} :: {$text}";
    }
    return $lines;
}
