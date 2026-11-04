<?php
/**
 * includes/llm.php — 생성 모델 호출 (Gemini / DeepSeek / Ollama), 동기와 스트리밍.
 *
 * 제공자는 LLM_PROVIDER 하나로 고르며 섞지 않는다. 제공자마다 다른 것은 세 가지뿐이다:
 * 요청(주소·헤더·본문), 동기 응답에서 본문 꺼내기, 스트리밍 조각에서 본문 꺼내기.
 * 이 세 가지를 제공자별 함수로 두고, 호출·재시도·시간 제한은 한 곳(call_llm, stream_llm)에서 처리한다.
 */

require_once __DIR__ . '/security.php';

const LLM_PROVIDERS = ['gemini', 'deepseek', 'ollama'];

/**
 * 생성 모델 제공자: LLM_PROVIDER = gemini(기본) | deepseek | ollama.
 * 예전 설정(GEMINI_MODEL = "deepseek-…")도 DeepSeek로 읽는다.
 */
function docent_llm_provider(): string {
    $provider = strtolower(trim((string)get_cfg('LLM_PROVIDER', '')));
    if ($provider === '') {
        $provider = str_starts_with(trim((string)get_cfg('GEMINI_MODEL', '')), 'deepseek') ? 'deepseek' : 'gemini';
    }
    if (!in_array($provider, LLM_PROVIDERS, true)) {
        throw new RuntimeException("LLM_PROVIDER must be 'gemini', 'deepseek', or 'ollama'.");
    }
    return $provider;
}

/** 지금 설정된 생성 모델 이름 (평가 메타데이터와 같은 값). */
function docent_llm_model(): string {
    switch (docent_llm_provider()) {
        case 'deepseek':
            $model = trim((string)get_cfg('DEEPSEEK_MODEL', ''));
            if ($model !== '') return $model;
            $legacy = trim((string)get_cfg('GEMINI_MODEL', ''));
            return str_starts_with($legacy, 'deepseek') ? $legacy : 'deepseek-chat';
        case 'ollama':
            $model = trim((string)get_cfg('OLLAMA_MODEL', ''));
            return $model !== '' ? $model : 'gemma4:26b';
        default:
            return trim((string)get_cfg('GEMINI_MODEL', 'gemini-flash-lite-latest'));
    }
}

/**
 * 시도할 모델 목록. Gemini는 서비스에서만 Gemini 대체 모델로 넘어가고, 평가 모드와 다른 제공자는
 * 설정한 모델 하나만 쓴다.
 */
function docent_llm_candidate_models(): array {
    $configured = docent_llm_model();
    $candidates = [$configured];
    if (docent_llm_provider() === 'gemini' && get_cfg('DOCENT_EVAL_MODE', '0') !== '1') {
        $candidates = array_merge($candidates, ['gemini-flash-lite-latest', 'gemini-2.5-flash-lite', 'gemini-2.5-flash', 'gemini-2.0-flash']);
    }
    $clean = [];
    foreach ($candidates as $cand) {
        $cand = trim((string)$cand);
        if ($cand !== '' && preg_match('/^[A-Za-z0-9._:-]+$/', $cand) && !in_array($cand, $clean, true)) {
            $clean[] = $cand;
        }
    }
    if (empty($clean)) throw new RuntimeException('Invalid LLM model configuration.');
    return $clean;
}

/** 제공자의 API 키. Ollama는 키가 없다. 키가 없으면 예외. */
function docent_llm_api_key(string $provider): string {
    if ($provider === 'ollama') return '';
    $key = $provider === 'deepseek'
        ? (string)get_cfg('DEEPSEEK_API_KEY', '')
        : (string)(get_cfg('GEMINI_API_KEY', '') ?: get_cfg('API_KEY', ''));
    if ($key === '') throw new RuntimeException(ucfirst($provider) . ' API key is not configured.');
    return $key;
}

/** 공통 생성 조건: 호출이 값을 주지 않으면 설정의 기본값. */
function _llm_sampling($max_tokens, $temperature, $top_p): array {
    return [
        'temperature' => is_numeric($temperature) ? (float)$temperature : (float)get_cfg('GEMINI_TEMPERATURE', '0.1'),
        'top_p' => is_numeric($top_p) ? (float)$top_p : (float)get_cfg('GEMINI_TOP_P', '0.85'),
        'max_tokens' => $max_tokens ? (int)max(1, min(8192, $max_tokens)) : 4096,
    ];
}

/** system/user/assistant 이외의 역할은 user로 보낸다. */
function _llm_chat_messages(array $msgs): array {
    $messages = [];
    foreach ($msgs as $m) {
        $role = in_array($m['role'] ?? 'user', ['system', 'assistant', 'user'], true) ? $m['role'] : 'user';
        $messages[] = ['role' => $role, 'content' => (string)($m['content'] ?? '')];
    }
    return $messages;
}

/**
 * 제공자별 요청: ['url', 'headers', 'body'(배열)].
 * Ollama는 동기와 스트리밍 모두 같은 /api/chat를 써서 문맥 창(num_ctx)과 시드가 늘 적용된다.
 */
function docent_llm_request(string $provider, string $model, string $key, array $msgs, bool $is_json, array $s, bool $stream): array {
    if ($provider === 'ollama') {
        $host = rtrim((string)get_cfg('OLLAMA_HOST', 'http://localhost:11434'), '/');
        $body = [
            'model' => $model,
            'messages' => _llm_chat_messages($msgs),
            'stream' => $stream,
            'think' => false,
            'options' => [
                'temperature' => $s['temperature'],
                'top_p' => $s['top_p'],
                'num_predict' => $s['max_tokens'],
                // 기본 문맥 창(2,048~4,096토큰)은 11,000자 근거 프롬프트의 앞부분을 말없이 잘라낸다.
                'num_ctx' => (int)get_cfg('OLLAMA_NUM_CTX', '16384'),
                'seed' => (int)get_cfg('OLLAMA_SEED', '42'),
            ],
        ];
        if ($is_json) $body['format'] = 'json';
        return ['url' => "{$host}/api/chat", 'headers' => ['Content-Type: application/json'], 'body' => $body];
    }
    if ($provider === 'deepseek') {
        return [
            'url' => 'https://api.deepseek.com/chat/completions',
            'headers' => ['Content-Type: application/json', "Authorization: Bearer {$key}"],
            'body' => [
                'model' => $model,
                'messages' => _llm_chat_messages($msgs),
                'temperature' => $s['temperature'],
                'top_p' => $s['top_p'],
                'max_tokens' => $s['max_tokens'],
                'stream' => $stream,
            ],
        ];
    }
    $system_prompt = '';
    $contents = [];
    foreach ($msgs as $m) {
        if (($m['role'] ?? '') === 'system') {
            $system_prompt = (string)$m['content'];
        } else {
            $role = (($m['role'] ?? '') === 'assistant') ? 'model' : 'user';
            $contents[] = ['role' => $role, 'parts' => [['text' => (string)($m['content'] ?? '')]]];
        }
    }
    $body = [
        'contents' => $contents,
        'generationConfig' => ['temperature' => $s['temperature'], 'topP' => $s['top_p'], 'maxOutputTokens' => $s['max_tokens']],
    ];
    if ($system_prompt !== '') $body['system_instruction'] = ['parts' => [['text' => $system_prompt]]];
    if ($is_json) $body['generationConfig']['response_mime_type'] = 'application/json';
    $endpoint = $stream ? 'streamGenerateContent?alt=sse' : 'generateContent';
    return [
        'url' => "https://generativelanguage.googleapis.com/v1beta/models/{$model}:{$endpoint}",
        'headers' => ['Content-Type: application/json', "x-goog-api-key: {$key}"],
        'body' => $body,
    ];
}

/** 동기 응답에서 [본문, 실제 모델 버전]. */
function docent_llm_parse_response(string $provider, string $res, string $model): array {
    $data = json_decode($res, true);
    if (!is_array($data)) return ['', $model];
    if ($provider === 'ollama') return [(string)($data['message']['content'] ?? ''), (string)($data['model'] ?? $model)];
    if ($provider === 'deepseek') return [(string)($data['choices'][0]['message']['content'] ?? ''), (string)($data['model'] ?? $model)];
    $content = '';
    foreach (($data['candidates'][0]['content']['parts'] ?? []) as $part) {
        if (isset($part['text'])) $content .= $part['text'];
    }
    return [$content, (string)($data['modelVersion'] ?? $model)];
}

/**
 * 스트리밍 응답의 한 조각(JSON 하나)에서 [본문 조각들, 끝났는지].
 * Gemini·DeepSeek는 SSE("data: …"), Ollama는 줄마다 JSON 하나다.
 */
function docent_llm_parse_chunk(string $provider, array $parsed): array {
    if ($provider === 'ollama') {
        $text = (string)($parsed['message']['content'] ?? '');
        return [$text !== '' ? [$text] : [], !empty($parsed['done'])];
    }
    if ($provider === 'deepseek') {
        $text = (string)($parsed['choices'][0]['delta']['content'] ?? '');
        return [$text !== '' ? [$text] : [], !empty($parsed['choices'][0]['finish_reason'])];
    }
    $texts = [];
    foreach (($parsed['candidates'][0]['content']['parts'] ?? []) as $part) {
        if (isset($part['text']) && $part['text'] !== '') $texts[] = $part['text'];
    }
    $finished = in_array($parsed['candidates'][0]['finishReason'] ?? '', ['STOP', 'MAX_TOKENS'], true);
    return [$texts, $finished];
}

/** JSON을 요청했는데 ```json …``` 으로 감싸 오면 안쪽만 쓴다. */
function _llm_unfence_json(string $content): string {
    return preg_match('/```(?:json)?\s*([\s\S]*?)```/i', $content, $m) ? trim($m[1]) : $content;
}

function _llm_encode(array $body): string {
    return json_encode($body, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_IGNORE);
}

/**
 * 동기 호출. 실패하면 (Gemini 서비스에서만) 다음 후보 모델로 넘어가며, 빈 답은 답으로 치지 않는다.
 */
function call_llm($msgs, $is_json = false, $max_tokens = null, $temperature = null, $top_p = null, $timeout = 60) {
    $provider = docent_llm_provider();
    $key = docent_llm_api_key($provider);
    $sampling = _llm_sampling($max_tokens, $temperature, $top_p);
    $start = microtime(true);
    $total_timeout = $provider === 'ollama' ? max(180, (int)$timeout) : max(5, (int)$timeout);
    @set_time_limit(max(300, $total_timeout + 60));

    foreach (docent_llm_candidate_models() as $model) {
        $remaining = $total_timeout - (microtime(true) - $start);
        if ($remaining <= 2.0) {
            error_log("call_llm: overall timeout reached ({$total_timeout}s), skipping remaining models");
            break;
        }
        $req = docent_llm_request($provider, $model, $key, $msgs, (bool)$is_json, $sampling, false);
        $ch = curl_init($req['url']);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => _llm_encode($req['body']),
            CURLOPT_HTTPHEADER => $req['headers'],
            CURLOPT_CONNECTTIMEOUT => max(3, min(6, (int)$remaining)),
            CURLOPT_TIMEOUT => (int)ceil($remaining),
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ]);
        $res = curl_exec($ch);
        $curl_errno = curl_errno($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

        if ($res === false || $curl_errno !== 0 || $http_code !== 200) {
            error_log(sprintf('LLM %s/%s failed: HTTP %d, cURL %d — trying next candidate', $provider, $model, $http_code, $curl_errno));
            if (($http_code === 429 || $http_code === 503) && ($total_timeout - (microtime(true) - $start)) > 3.0) {
                usleep(600000); // 0.6초 백오프 후 다음 모델 시도
            }
            continue;
        }
        [$content, $version] = docent_llm_parse_response($provider, (string)$res, $model);
        if ($is_json) $content = _llm_unfence_json($content);
        if (trim($content) !== '') {
            $GLOBALS['docent_model_versions'][$version] = ($GLOBALS['docent_model_versions'][$version] ?? 0) + 1;
            return $content;
        }
    }
    throw new RuntimeException('LLM generation failed.');
}

/**
 * 스트리밍 호출. 끝 표시까지 받은 경우만 성공이다. 일부를 보낸 뒤 끊기면 다른 모델의 답을 이어 붙이지 않고 예외.
 */
function stream_llm($msgs, callable $on_chunk, $max_tokens = 8192, $temperature = null, $top_p = null) {
    $provider = docent_llm_provider();
    $key = docent_llm_api_key($provider);
    $sampling = _llm_sampling($max_tokens, $temperature, $top_p);
    // SSE는 빈 줄로 사건을 나누고, Ollama는 줄마다 JSON 하나를 보낸다.
    $separator = $provider === 'ollama' ? '/^(.*?)\r?\n/s' : '/^(.*?)(?:\r?\n\r?\n)/s';

    foreach (docent_llm_candidate_models() as $model) {
        $req = docent_llm_request($provider, $model, $key, $msgs, false, $sampling, true);
        $buffer = '';
        $delivered = 0;
        $finished = false;
        $consume = function(string $block) use (&$delivered, &$finished, $on_chunk, $provider) {
            foreach (preg_split('/\r?\n/', $block) as $line) {
                $line = trim($line);
                if ($provider !== 'ollama') {
                    if (!str_starts_with($line, 'data: ')) continue;
                    $line = trim(substr($line, 6));
                    if ($line === '[DONE]') { $finished = true; continue; }
                }
                $parsed = json_decode($line, true);
                if (!is_array($parsed)) continue;
                [$texts, $done] = docent_llm_parse_chunk($provider, $parsed);
                foreach ($texts as $text) { $delivered++; $on_chunk($text); }
                if ($done) $finished = true;
            }
        };
        $ch = curl_init($req['url']);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => _llm_encode($req['body']),
            CURLOPT_HTTPHEADER => $req['headers'],
            CURLOPT_CONNECTTIMEOUT => 8,
            CURLOPT_TIMEOUT => $provider === 'ollama' ? 300 : 90,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_WRITEFUNCTION => function($ch, $data) use (&$buffer, $consume, $separator) {
                $buffer .= $data;
                while (preg_match($separator, $buffer, $matches)) {
                    $buffer = substr($buffer, strlen($matches[0]));
                    $consume($matches[1]);
                }
                return strlen($data);
            },
        ]);
        curl_exec($ch);
        if (trim($buffer) !== '') $consume($buffer); // 마지막 줄에 줄바꿈이 없을 때
        $curl_errno = curl_errno($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

        if ($delivered > 0 && $finished && $curl_errno === 0 && $http_code === 200) return;
        // Never concatenate a new model's answer after an interrupted partial response.
        if ($delivered > 0) throw new RuntimeException('LLM stream interrupted.');
        error_log(sprintf('stream_llm %s/%s failed (HTTP %d, cURL %d) — trying next model', $provider, $model, $http_code, $curl_errno));
    }
    throw new RuntimeException('LLM streaming generation failed.');
}
