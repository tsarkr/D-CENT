<?php
/**
 * includes/gemini.php — Gemini API 동기/스트리밍 호출
 */

require_once __DIR__ . '/security.php';

function get_gemini_candidate_models() {
    $configured = trim((string)get_cfg('GEMINI_MODEL', 'gemini-flash-lite-latest'));
    $candidates = [
        $configured,
        'gemini-flash-lite-latest',
        'gemini-2.5-flash-lite',
        'gemini-2.5-flash',
        'gemini-2.0-flash',
    ];
    if (get_cfg('DOCENT_EVAL_MODE', '0') === '1') $candidates = [$configured];
    $clean = [];
    foreach ($candidates as $cand) {
        $cand = trim((string)$cand);
        if ($cand !== '' && preg_match('/^[A-Za-z0-9._-]+$/', $cand) && !in_array($cand, $clean, true)) {
            $clean[] = $cand;
        }
    }
    if (empty($clean)) throw new RuntimeException('Invalid Gemini model configuration.');
    return $clean;
}

/**
 * 동기식 Gemini API 호출 (model fallback 포함)
 */
function call_gemini($msgs, $is_json = false, $max_tokens = null, $temperature = null, $top_p = null, $timeout = 60) {
    $api_key = get_cfg('GEMINI_API_KEY') ?: get_cfg('API_KEY');
    if (!$api_key) throw new RuntimeException("Gemini API key is not configured.");

    $candidate_models = get_gemini_candidate_models();
    $payload = _build_gemini_payload($msgs, $is_json, $max_tokens, $temperature, $top_p);
    $post_json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_IGNORE);

    $start_time = microtime(true);
    $total_timeout = max(5, (int)$timeout);

    foreach ($candidate_models as $model) {
        $elapsed = microtime(true) - $start_time;
        $remaining = $total_timeout - $elapsed;
        if ($remaining <= 2.0) {
            error_log("call_gemini: overall timeout reached ({$total_timeout}s), skipping remaining models");
            break;
        }

        $url = "https://generativelanguage.googleapis.com/v1beta/models/{$model}:generateContent";
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $post_json,
            CURLOPT_HTTPHEADER => ["Content-Type: application/json", "x-goog-api-key: {$api_key}"],
            CURLOPT_CONNECTTIMEOUT => max(3, min(6, (int)$remaining)),
            CURLOPT_TIMEOUT => (int)ceil($remaining),
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
        ]);

        $res = curl_exec($ch);
        $curl_errno = curl_errno($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

        if ($res === false || $curl_errno !== 0 || $http_code !== 200) {
            error_log(sprintf('Gemini model %s failed: HTTP %d, cURL %d — trying next candidate', $model, $http_code, $curl_errno));
            if ($http_code === 429 || $http_code === 503) {
                if (($total_timeout - (microtime(true) - $start_time)) > 3.0) {
                    usleep(600000); // 0.6초 백오프 후 다음 모델 시도
                }
            }
            continue;
        }

        $content = _extract_gemini_text($res);
        if (trim($content) !== '') {
            $data = json_decode($res, true);
            $version = $data['modelVersion'] ?? $model;
            $GLOBALS['docent_model_versions'][$version] = ($GLOBALS['docent_model_versions'][$version] ?? 0) + 1;
            return $content;
        }
    }

    throw new RuntimeException('Gemini generation failed.');
}

/**
 * SSE 스트리밍 Gemini API 호출
 */
function stream_gemini($msgs, callable $on_chunk, $max_tokens = 8192, $temperature = null, $top_p = null) {
    $api_key = get_cfg('GEMINI_API_KEY') ?: get_cfg('API_KEY');
    if (!$api_key) {
        throw new RuntimeException("Gemini API key is not configured.");
    }

    $candidate_models = get_gemini_candidate_models();
    $payload = _build_gemini_payload($msgs, false, $max_tokens, $temperature, $top_p);
    $post_json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_IGNORE);

    foreach ($candidate_models as $model) {
        $url = "https://generativelanguage.googleapis.com/v1beta/models/{$model}:streamGenerateContent?alt=sse";
        $buffer = '';
        $chunks_delivered = 0;
        $finished = false;

        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $post_json,
            CURLOPT_HTTPHEADER => ["Content-Type: application/json", "x-goog-api-key: {$api_key}"],
            CURLOPT_CONNECTTIMEOUT => 8,
            CURLOPT_TIMEOUT => 90,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_WRITEFUNCTION => function($ch, $data) use (&$buffer, &$chunks_delivered, &$finished, $on_chunk) {
                $buffer .= $data;
                while (preg_match('/^(.*?)(?:\r?\n\r?\n)/s', $buffer, $matches)) {
                    $block = $matches[1];
                    $buffer = substr($buffer, strlen($matches[0]));
                    $lines = preg_split('/\r?\n/', $block);
                    foreach ($lines as $line) {
                        $line = trim($line);
                        if (str_starts_with($line, 'data: ')) {
                            $parsed = json_decode(substr($line, 6), true);
                            if (in_array($parsed['candidates'][0]['finishReason'] ?? '', ['STOP', 'MAX_TOKENS'], true)) $finished = true;
                            if (isset($parsed['candidates'][0]['content']['parts'])) {
                                foreach ($parsed['candidates'][0]['content']['parts'] as $part) {
                                    if (isset($part['text']) && $part['text'] !== '') {
                                        $chunks_delivered++;
                                        $on_chunk($part['text']);
                                    }
                                }
                            }
                        }
                    }
                }
                return strlen($data);
            }
        ]);

        curl_exec($ch);
        $curl_errno = curl_errno($ch);
        $http_code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

        if ($chunks_delivered > 0 && $finished && $curl_errno === 0 && $http_code === 200) return;
        // Never concatenate a new model's answer after an interrupted partial response.
        if ($chunks_delivered > 0) throw new RuntimeException('Gemini stream interrupted.');

        error_log(sprintf('stream_gemini model %s failed (HTTP %d, cURL %d) — trying next model', $model, $http_code, $curl_errno));
    }

    throw new RuntimeException('Gemini streaming generation failed.');
}

// ── 내부 헬퍼 ──
function _build_gemini_payload($msgs, $is_json, $max_tokens, $temperature, $top_p) {
    $system_prompt = "";
    $contents = [];
    foreach ($msgs as $m) {
        if ($m['role'] === 'system') {
            $system_prompt = $m['content'];
        } else {
            $role = ($m['role'] === 'assistant') ? 'model' : 'user';
            $contents[] = ["role" => $role, "parts" => [["text" => $m['content']]]];
        }
    }

    $default_temp = (float)get_cfg('GEMINI_TEMPERATURE', '0.1');
    $default_topp = (float)get_cfg('GEMINI_TOP_P', '0.85');

    $payload = [
        "contents" => $contents,
        "generationConfig" => [
            "temperature" => is_numeric($temperature) ? (float)$temperature : $default_temp,
            "topP" => is_numeric($top_p) ? (float)$top_p : $default_topp,
            "maxOutputTokens" => $max_tokens ? (int)max(1, min(8192, $max_tokens)) : 4096,
        ]
    ];

    if ($system_prompt !== "") {
        $payload["system_instruction"] = ["parts" => [["text" => $system_prompt]]];
    }
    if ($is_json) {
        $payload["generationConfig"]["response_mime_type"] = "application/json";
    }
    return $payload;
}

function _extract_gemini_text($json_response) {
    $data = json_decode($json_response, true);
    $content = '';
    if (isset($data['candidates'][0]['content']['parts']) && is_array($data['candidates'][0]['content']['parts'])) {
        foreach ($data['candidates'][0]['content']['parts'] as $part) {
            if (isset($part['text'])) $content .= $part['text'];
        }
    }
    return $content;
}
