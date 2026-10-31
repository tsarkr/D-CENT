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
    if (isset($handle->options[CURLOPT_WRITEFUNCTION])) {
        if ($GLOBALS['transport'] === 'unfinished') unset($response['candidates'][0]['finishReason']);
        ($handle->options[CURLOPT_WRITEFUNCTION])($handle, 'data: ' . json_encode($response) . "\n\n");
        return true;
    }
    if ($GLOBALS['transport'] === 'failed') return false;
    return json_encode($response);
}
function curl_errno($handle) { return in_array($GLOBALS['transport'], ['ok', 'unfinished'], true) ? 0 : 18; }
function curl_getinfo($handle, $option) { return 200; }
// Evaluate the production functions in a namespace so only their cURL calls are mocked.
$gemini = file_get_contents(__DIR__ . '/../includes/gemini.php');
$gemini = str_replace("require_once __DIR__ . '/security.php';", '', $gemini);
eval('namespace DocentSafety; use \\RuntimeException;' . substr($gemini, 5));
$helpers = file_get_contents(__DIR__ . '/../includes/helpers.php');
eval('namespace DocentSafety; use \\RuntimeException;' . substr($helpers, 5));
check(get_gemini_candidate_models() === ['configured-model'], 'Evaluation must not switch models');
$GLOBALS['calls'] = 0;
$GLOBALS['transport'] = 'ok';
check(call_gemini([]) === 'answer', 'Synchronous response');
check($GLOBALS['docent_model_versions']['dated-test-model'] === 1, 'Actual model version recorded');
$GLOBALS['transport'] = 'failed';
try { call_gemini([]); throw new LogicException('Failure accepted as answer'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Failure must throw'); }
$settings['DOCENT_EVAL_MODE'] = '0';
$GLOBALS['calls'] = 0;
$GLOBALS['transport'] = 'partial';
$text = '';
try {
    stream_gemini([], function($chunk) use (&$text) { $text .= $chunk; });
    throw new LogicException('Partial stream accepted');
} catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Partial stream must throw'); }
check($text === 'answer' && $GLOBALS['calls'] === 1, 'Partial output must not be followed by another model');
$GLOBALS['transport'] = 'unfinished';
$GLOBALS['calls'] = 0;
try { stream_gemini([], function($chunk) {}); throw new LogicException('Missing finish marker accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Missing finish marker must throw'); }
check($GLOBALS['calls'] === 1, 'Unfinished output must not trigger model fallback');
$GLOBALS['transport'] = 'ok';
$text = '';
stream_gemini([], function($chunk) use (&$text) { $text .= $chunk; });
check($text === 'answer', 'Finished stream must succeed');
$settings['GEMINI_API_KEY'] = '';
try { call_gemini([]); throw new LogicException('Missing key accepted'); }
catch (RuntimeException $e) { check(!($e instanceof LogicException), 'Missing key must throw'); }
$context = "narrative\n\n<SOURCE_EVIDENCE id=\"raw_event_info-1\">first</SOURCE_EVIDENCE>\n\n<SOURCE_EVIDENCE id=\"raw_event_info-2\">second</SOURCE_EVIDENCE>";
$items = docent_context_items($context);
check(count($items) === 3 && $items[0] === 'narrative', 'Evidence order must be preserved');
echo "Gemini and context safety checks passed\n";
