<?php
// Offline check of the graph facts (incident route, region timeline, fact lines) and the context assembly:
// a fake Neo4j client, no database.
namespace Laudis\Neo4j\Databags {
    class TransactionConfiguration { static function default() { return new self; } function withTimeout($t) { return $this; } }
}
namespace {
function check($condition, $message) { if (!$condition) { fwrite(STDERR, "FAIL: {$message}\n"); exit(1); } }
// Load only the functions under test from includes/actions.php.
$src = file_get_contents(__DIR__ . '/../includes/actions.php');
preg_match_all('/^(?:const GRAPHRAG_[A-Z_]+ = [^\n]+|function (?:docent_record_keys|docent_graph_facts|docent_graph_record_keys|docent_register_keys|graphrag_compare_by_date|graphrag_region_timeline|graphrag_incident_rows|graphrag_incidents_by_name|graphrag_run_with_timeout)\(.*?^\})/ms', $src, $m);
check(count($m[0]) === 12, 'functions and constants of the graph facts found');
function get_pg() { return null; }
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

// Fact lines: one line per incident, the question's person marked as not recorded, record keys kept.
$facts = docent_graph_facts(['rows' => $rows, 'persons' => ['안창호'], 'timeline' => null], 'ko');
check(str_starts_with($facts, '[그래프 사실표]'), 'header');
check(str_contains($facts, '사건 「3월 1일 평남 평양부 독립선언식 시위」 (1919-03-01) · 관련 인물: 金善斗(김선두)'), 'incident line with its persons');
check(str_contains($facts, '관련 사건: 「3월 2일 전후 평남 평양부 격문 배포」(1919-03-02)'), 'related incident on the incident line');
check(str_contains($facts, '근거: raw_event_info-5'), 'record key on the incident line');
check(str_contains($facts, '질문의 인물 중 관련 인물로 기록되지 않음: 안창호'), 'unsupported premise stated by the graph');
check(str_contains($facts, '인물 「안창호」: 그래프에서 관련 인물로 연결된 사건을 찾지 못함'), 'person without incidents');
check(str_contains($facts, '사건 「3월 1일 평남 평양부 독립선언서 배포」 (1919-03-01) · 관련 인물 기록 없음'), 'incident without persons');
check(docent_graph_record_keys($facts) === ['raw_event_info-5', 'raw_source_info-11'], 'cited record keys in order');
check(graphrag_incident_rows($client, '짧음') === [], 'short questions skip the lookup');
// Region timeline: the most specific region, its count, and the incidents in date order.
$tl_client = new FakeClient([[new Rec(['region' => '전라북도 진안군', 'total' => 3, 'items' => [
    ['id' => 'A1', 'name' => '전북 진안군 독립선언서 배포', 'date' => '1919-03-01'],
    ['id' => 'A2', 'name' => '4월 13일 전북 진안군 성수면 도통리 시위', 'date' => '1919-04-13']]])]]);
$tl = graphrag_region_timeline($tl_client, '1919년 전라북도 진안군에서 기록된 시위는 몇 건인가?', 5.0);
check($tl['region'] === '전라북도 진안군' && $tl['total'] === 3 && count($tl['items']) === 2, 'timeline read');
$facts = docent_graph_facts(['rows' => [], 'persons' => [], 'timeline' => $tl], 'ko');
check(str_contains($facts, '지역 「전라북도 진안군」: 사건 대장의 사건 3건 (날짜순, 앞의 2건만 표시)'), 'count line');
check(str_contains($facts, '  2. 1919-04-13 「4월 13일 전북 진안군 성수면 도통리 시위」'), 'dated list');
check(graphrag_region_timeline(new FakeClient([[]]), '1919년 전라북도 진안군', 5.0) === null, 'no region: no timeline');
check(docent_graph_facts(['rows' => [], 'persons' => [], 'timeline' => null], 'ko') === '', 'nothing known: no block');
require_once __DIR__ . '/../includes/helpers.php';
// Context assembly: graph facts first (cut at a line), then records in the hybrid baseline's block format.
$recs = [];
for ($i = 10; $i < 40; $i++) $recs[] = "[public.raw_source_info] node=기록검색 rowid={$i} :: " . str_repeat('기록 원문 ', 60);
$graph = "[그래프 사실표]\n" . implode("\n", array_fill(0, 200, '사건 「가」 · 관련 인물: 나'));
$assembled = docent_assemble_context($graph, $recs, 11000, 2500);
$ctx = $assembled['context'];
check(mb_strlen($ctx) <= 11000, 'total budget respected');
check($assembled['graph_chars'] <= 2500 && str_starts_with($ctx, '[그래프 사실표]'), 'graph facts first, within their cap');
check(str_ends_with(mb_substr($ctx, 0, $assembled['graph_chars']), '관련 인물: 나'), 'graph facts cut at a line boundary');
check(str_contains($ctx, "<SOURCE_EVIDENCE id=\"raw_source_info-10\">\n기록 원문"), 'record block format of the hybrid baseline');
check(str_ends_with($ctx, '</SOURCE_EVIDENCE>'), 'last record keeps its closing tag');
$dup = docent_assemble_context('', [$recs[0], $recs[0], 'malformed line'], 11000, 2500);
check(substr_count($dup['context'], '<SOURCE_EVIDENCE') === 1 && $dup['graph_chars'] === 0, 'duplicates and malformed lines dropped');
echo "Graph facts and context assembly checks passed\n";
}
