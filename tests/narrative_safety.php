<?php
// Offline check of the incident route of the narrative paths: a fake Neo4j client, no database.
namespace Laudis\Neo4j\Databags {
    class TransactionConfiguration { static function default() { return new self; } function withTimeout($t) { return $this; } }
}
namespace {
function check($condition, $message) { if (!$condition) { fwrite(STDERR, "FAIL: {$message}\n"); exit(1); } }
// Load only the functions under test from includes/actions.php.
$src = file_get_contents(__DIR__ . '/../includes/actions.php');
preg_match_all('/^(?:const GRAPHRAG_[A-Z_]+ = [^\n]+|function (?:docent_record_keys|graphrag_serialize_results|graphrag_incident_rows|graphrag_run_with_timeout)\(.*?^\})/ms', $src, $m);
check(count($m[0]) === 6, 'functions and constants of the incident route found');
eval(implode("\n", $m[0]));

class Rec { private array $d; function __construct(array $d) { $this->d = $d; } function get($k) { return $this->d[$k] ?? null; } }
class FakeClient {
    private int $n = 0; private array $answers;
    function __construct(array $answers) { $this->answers = $answers; }
    function readTransaction($fn, $x, $cfg) { return $this->answers[$this->n++] ?? []; }
}
$client = new FakeClient([
    [new Rec(['id' => 'E2', 'name' => '3월 1일 평남 평양부 독립선언식 시위', 'date' => '1919-03-01']),
     new Rec(['id' => 'E1', 'name' => '3월 1일 평남 평양부 독립선언서 배포', 'date' => '1919-03-01']),
     new Rec(['id' => 'E9', 'name' => '평양부 독립선언서 배포', 'date' => '1919-03-01'])],
    [new Rec(['docs' => ['raw_source_info-10'],
              'people' => [['person' => '金善斗', 'reading' => '김선두', 'sources' => ['raw_event_info-5']],
                           ['person' => '김선두', 'reading' => '김선두', 'sources' => ['raw_event_info-5']],
                           ['person' => null, 'reading' => null, 'sources' => []]],
              'related' => [['name' => '3월 2일 전후 평남 평양부 격문 배포', 'date' => '1919-03-02']]])],
    [new Rec(['docs' => ['raw_source_info-11'], 'people' => [], 'related' => []])],
]);
$rows = graphrag_incident_rows($client, "'3월 1일 평남 평양부 독립선언서 배포'와 '3월 1일 평남 평양부 독립선언식 시위'의 관련 인물은?");
check(count($rows) === 3, 'one participant (Hanja and Hangul nodes counted once), one related incident, one incident without persons');
check($rows[0]['연관인물'] === '金善斗(김선두)' && $rows[0]['근거'] === ['raw_event_info-5'], 'Hanja name with its reading, attesting record kept');
check($rows[1]['유형'] === 'related' && $rows[1]['공통사건'] === '3월 1일 평남 평양부 독립선언식 시위', 'related incident row');
check($rows[2]['연관인물'] === '' && $rows[2]['근거'] === ['raw_source_info-11'], 'incident without persons cites its documents');
check(!in_array('평양부 독립선언서 배포', array_column($rows, '사건이름'), true), 'name contained in a longer match is dropped');

$block = graphrag_serialize_results($rows, '평양', 'ko');
check(str_contains($block, 'id="raw_event_info-5"'), 'block cited by its record');
check(!str_contains($block, 'id="graphrag_'), 'no internal block label when a record or incident is known');
check(str_contains($block, '관련 사건으로 연결합니다'), 'related incident wording');
check(graphrag_incident_rows($client, '짧음') === [], 'short questions skip the lookup');
require_once __DIR__ . '/../includes/helpers.php';
$records = [
    '[public.raw_event_info] node=사건 rowid=1 제목: ' . str_repeat('긴제목', 300) . '; 내용: 기록',
    '[public.raw_event_info] node=사건 rowid=2 제목: 시위; 내용: 파리강화회의 참여 이후 시위 관련 인물은 김철수이다.',
];
$context = build_budgeted_pg_context($records, 'ko', 25, 30, 450, 700, '시위');
check(str_contains($context, 'raw_event_info-2'), 'oversized record does not suppress later records');
check(str_contains($context, '김철수'), 'diplomatic keywords do not erase source facts');
check(mb_strlen($context) <= 700, 'context respects character budget');
$long = '[public.raw_event_info] node=사건 rowid=3 ' . str_repeat('본문 ', 300);
$context = build_budgeted_pg_context([$long], 'ko', 25, 30, 450, 400);
check(str_contains($context, 'raw_event_info-3'), 'body is fitted to actual envelope overhead');
check(str_ends_with($context, '</SOURCE_EVIDENCE>') && mb_strlen($context) <= 400, 'trimmed source remains complete');
echo "Narrative incident route checks passed\n";
}
