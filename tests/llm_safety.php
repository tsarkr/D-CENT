<?php
namespace DocentSafety;
use RuntimeException;
use LogicException;
use stdClass;
// Fake namespaced cURL transport: no configuration files or external requests.
$_SESSION = ['docent_csrf' => 'offline-test'];
$settings = ['GEMINI_API_KEY' => 'offline-test', 'GEMINI_MODEL' => 'configured-model', 'DOCENT_EVAL_MODE' => '1'];
function get_cfg($key, $default = '') { return $GLOBALS['settings'][$key] ?? $default; }
function check($condition, $message) { if (!$condition) throw new RuntimeException($message); }
$constants = ['CURLOPT_RETURNTRANSFER', 'CURLOPT_POST', 'CURLOPT_POSTFIELDS', 'CURLOPT_HTTPHEADER',
    'CURLOPT_CONNECTTIMEOUT', 'CURLOPT_TIMEOUT', 'CURLOPT_SSL_VERIFYPEER', 'CURLOPT_SSL_VERIFYHOST',
    'CURLOPT_WRITEFUNCTION', 'CURLINFO_RESPONSE_CODE'];
foreach ($constants as $i => $name) if (!defined($name)) define($name, $i + 1);
function curl_init($url) { $GLOBALS['calls']++; return new stdClass(); }
function curl_setopt_array($handle, $options) { $handle->options = $options; }
function curl_exec($handle) {
    $response = ['modelVersion' => 'dated-test-model', 'candidates' => [['finishReason' => 'STOP', 'content' => ['parts' => [['text' => 'answer']]]]]];
    if (isset($handle->options[CURLOPT_WRITEFUNCTION]) && $GLOBALS['transport'] === 'ollama') {
        $w = $handle->options[CURLOPT_WRITEFUNCTION];
        $w($handle, json_encode(['message' => ['content' => 'ans'], 'done' => false]) . "\n");
        $w($handle, json_encode(['message' => ['content' => 'wer'], 'done' => true]));
        return true;
    }
    if (isset($handle->options[CURLOPT_WRITEFUNCTION])) {
        if ($GLOBALS['transport'] === 'unfinished') unset($response['candidates'][0]['finishReason']);
        ($handle->options[CURLOPT_WRITEFUNCTION])($handle, 'data: ' . json_encode($response) . "\n\n");
        return true;
    }
    if ($GLOBALS['transport'] === 'retriever') {
        return json_encode(['results' => [
            ['table' => 'raw_source_info', 'rowid' => '12', 'chunk' => 'raw_source_info:12#0', 'text' => "보성사에서  선언서를\n인쇄"],
            ['table' => 'raw_source_info', 'rowid' => '12', 'chunk' => 'raw_source_info:12#0', 'text' => '보성사에서 선언서를 인쇄'],
            ['table' => 'bad table', 'rowid' => '1', 'chunk' => 'x', 'text' => 'dropped'],
        ]]);
    }
    if ($GLOBALS['transport'] === 'failed') return false;
    return json_encode($response);
}
function curl_errno($handle) { return in_array($GLOBALS['transport'], ['ok', 'unfinished', 'ollama', 'retriever'], true) ? 0 : 18; }
function curl_getinfo($handle, $option) { return 200; }
// Evaluate the production functions in a namespace so only their cURL calls are mocked.
$llm = file_get_contents(__DIR__ . '/../includes/llm.php');
$llm = str_replace("require_once __DIR__ . '/security.php';", '', $llm);
eval('namespace DocentSafety; use \\RuntimeException;' . substr($llm, 5));
$retriever = file_get_contents(__DIR__ . '/../includes/retriever.php');
$retriever = str_replace("require_once __DIR__ . '/security.php';", '', $retriever);
eval('namespace DocentSafety; use \\RuntimeException;' . substr($retriever, 5));
$helpers = file_get_contents(__DIR__ . '/../includes/helpers.php');
eval('namespace DocentSafety; use \\RuntimeException;' . substr($helpers, 5));
check(docent_llm_candidate_models() === ['configured-model'], 'Evaluation must not switch models');
$GLOBALS['calls'] = 0;
$GLOBALS['transport'] = 'ok';
check(call_llm([]) === 'answer', 'Synchronous response');
check($GLOBALS['docent_model_versions']['dated-test-model'] === 1, 'Actual model version recorded');
$GLOBALS['transport'] = 'failed';
try { call_llm([]); throw new LogicException('Failure accepted as answer'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Failure must throw'); }
$settings['DOCENT_EVAL_MODE'] = '0';
$GLOBALS['calls'] = 0;
$GLOBALS['transport'] = 'partial';
$text = '';
try {
    stream_llm([], function($chunk) use (&$text) { $text .= $chunk; });
    throw new LogicException('Partial stream accepted');
} catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Partial stream must throw'); }
check($text === 'answer' && $GLOBALS['calls'] === 1, 'Partial output must not be followed by another model');
$GLOBALS['transport'] = 'unfinished';
$GLOBALS['calls'] = 0;
try { stream_llm([], function($chunk) {}); throw new LogicException('Missing finish marker accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Missing finish marker must throw'); }
check($GLOBALS['calls'] === 1, 'Unfinished output must not trigger model fallback');
$GLOBALS['transport'] = 'ok';
$text = '';
stream_llm([], function($chunk) use (&$text) { $text .= $chunk; });
check($text === 'answer', 'Finished stream must succeed');
$settings['GEMINI_API_KEY'] = '';
try { call_llm([]); throw new LogicException('Missing key accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Missing key must throw'); }
$context = "narrative\n\n<SOURCE_EVIDENCE id=\"raw_event_info-1\">first</SOURCE_EVIDENCE>\n\n<SOURCE_EVIDENCE id=\"raw_event_info-2\">second</SOURCE_EVIDENCE>";
$items = docent_context_items($context);
check(count($items) === 3 && $items[0] === 'narrative', 'Evidence order must be preserved');
// Provider switch: the providers are never mixed, and the old GEMINI_MODEL = "deepseek-…" still selects DeepSeek.
$settings = ['GEMINI_API_KEY' => 'k', 'DEEPSEEK_API_KEY' => 'k', 'GEMINI_MODEL' => 'gemini-test', 'DOCENT_EVAL_MODE' => '0'];
check(docent_llm_provider() === 'gemini', 'Gemini is the default provider');
check(!in_array('deepseek-chat', docent_llm_candidate_models(), true), 'Gemini mode must not fall back to DeepSeek');
$settings['LLM_PROVIDER'] = 'deepseek';
$settings['DEEPSEEK_MODEL'] = 'deepseek-test';
check(docent_llm_candidate_models() === ['deepseek-test'], 'DeepSeek mode uses only DEEPSEEK_MODEL');
check(docent_llm_model() === 'deepseek-test', 'Reported model is the DeepSeek model');
$settings = ['GEMINI_MODEL' => 'deepseek-chat'];
check(docent_llm_provider() === 'deepseek' && docent_llm_model() === 'deepseek-chat', 'Legacy DeepSeek setting');
$settings = ['LLM_PROVIDER' => 'other'];
try { docent_llm_provider(); throw new LogicException('Unknown provider accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Unknown provider must throw'); }
// Request shapes: Ollama uses /api/chat with the same context window and seed for both call kinds.
$settings = ['LLM_PROVIDER' => 'ollama', 'OLLAMA_MODEL' => 'local-test', 'OLLAMA_NUM_CTX' => '16384'];
$s = _llm_sampling(100, 0.0, 0.8);
foreach ([false, true] as $stream) {
    $req = docent_llm_request('ollama', 'local-test', '', [['role' => 'system', 'content' => 'sys'], ['role' => 'user', 'content' => 'q']], false, $s, $stream);
    check(str_ends_with($req['url'], '/api/chat'), 'Ollama always uses /api/chat');
    check($req['body']['options']['num_ctx'] === 16384 && $req['body']['options']['seed'] === 42, 'Ollama context window and seed');
    check($req['body']['stream'] === $stream && $req['body']['think'] === false, 'Ollama stream flag and no thinking');
}
check(docent_llm_api_key('ollama') === '', 'Ollama needs no key');
$req = docent_llm_request('gemini', 'g', 'k', [['role' => 'system', 'content' => 'sys'], ['role' => 'assistant', 'content' => 'a']], true, $s, false);
check($req['body']['system_instruction']['parts'][0]['text'] === 'sys' && $req['body']['contents'][0]['role'] === 'model', 'Gemini roles');
check($req['body']['generationConfig']['response_mime_type'] === 'application/json', 'Gemini JSON mode');
check(_llm_unfence_json("```json\n{\"a\":1}\n```") === '{"a":1}', 'Fenced JSON is unwrapped');
// Ollama streams one JSON object per line, and the last line need not end with a newline.
$GLOBALS['transport'] = 'ollama';
$GLOBALS['calls'] = 0;
$text = '';
stream_llm([], function($chunk) use (&$text) { $text .= $chunk; });
check($text === 'answer' && $GLOBALS['calls'] === 1, 'Ollama line stream');
// Record retriever: ranked chunks become pg_text lines; duplicates and malformed rows are dropped.
$settings = ['DOCENT_RETRIEVER_URL' => 'http://127.0.0.1:8765'];
$GLOBALS['transport'] = 'retriever';
$lines = docent_retrieve_records('보성사', true);
check($lines === ['[public.raw_source_info] node=기록검색 rowid=12 :: 보성사에서 선언서를 인쇄'], 'Retriever lines');
$GLOBALS['transport'] = 'failed';
check(docent_retrieve_records('보성사', false) === [], 'Site continues without the retriever');
try { docent_retrieve_records('보성사', true); throw new LogicException('Evaluation without retriever accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Evaluation needs the retriever'); }
$settings = [];
try { docent_retrieve_records('보성사', true); throw new LogicException('Unset retriever accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Unset retriever fails in evaluation'); }
check(docent_retrieve_records('보성사', false) === [], 'Unset retriever is skipped on the site');
echo "LLM and context safety checks passed\n";
