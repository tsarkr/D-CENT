<?php
/**
 * includes/helpers.php — 노드맵, 엣지, evidence, merge, prefetch 유틸리티
 */

function clamp_int($value, $min, $max) {
    return max($min, min($max, (int)$value));
}

function normalize_whitespace_text($text) {
    $text = preg_replace('/\s+/u', ' ', (string)$text);
    return trim($text ?? '');
}

// ── 노드 → vis.js 맵 추가 ──
function add_node_to_map(&$map, $node, $labels_iterable) {
    if (!$node) return null;

    $props = [];
    if (method_exists($node, 'getProperties')) {
        foreach ($node->getProperties() as $k => $v) {
            if ($k === 'embedding') continue;
            $props[$k] = is_scalar($v) ? $v : (is_iterable($v) ? json_encode($v, JSON_UNESCAPED_UNICODE) : (string)$v);
        }
    }

    $labels_list = [];
    if (is_iterable($labels_iterable)) {
        foreach ($labels_iterable as $l) $labels_list[] = (string)$l;
    }

    $raw_id = $props['uid'] ?? $props['id'] ?? $props['명칭'] ?? $props['name'] ?? $props['title'] ?? 'unknown';
    $nid = 'n' . substr(sha1((string)$raw_id), 0, 12);

    if (!isset($map[$nid])) {
        $reading = $props['한글독음'] ?? '';
        $base = $props['제목'] ?? $props['명칭'] ?? $props['사건명'] ?? $raw_id;
        // 한자 명칭에 독음이 없으면 한글 명칭(기관·행정구역의 원천 한글명)을 독음 대신 표시
        if ($reading === '' && preg_match('/\p{Han}/u', (string)$base)) {
            $h_name = (string)($props['한글명칭'] ?? '');
            if ($h_name !== '' && !preg_match('/\p{Han}/u', $h_name)) $reading = $h_name;
        }

        // 유령 개체 필터링
        static $ghost_stopwords = ['대표', '달하', '그러', '총독', '순사', '공판', '십자표', '펼치는데', '연행하', '거행하고'];
        if (in_array((string)$base, $ghost_stopwords, true) && empty($props['한글독음']) && empty($props['uid']) && empty($props['id'])) {
            return null;
        }

        $label_text = ($reading && $base != $reading) ? "{$base} ({$reading})" : $base;

        // 노드 스타일 결정
        $color = "#999999"; $icon = "";
        $lstr = implode(' ', $labels_list);
        if (strpos($lstr, '문건') !== false || strpos($lstr, '사료') !== false) { $color = "#F7A01F"; $icon = "📜\n"; }
        elseif (strpos($lstr, '인물') !== false) { $color = "#2563EB"; $icon = "👤\n"; }
        elseif (strpos($lstr, '사건') !== false) { $color = "#DC2626"; $icon = "🔥\n"; }
        elseif (strpos($lstr, '장소') !== false) { $color = "#16A34A"; $icon = "📍\n"; }
        elseif (strpos($lstr, '기관') !== false) { $color = "#7C3AED"; $icon = "🏢\n"; }

        // 타입 추론
        $type = (string)($props['type'] ?? '');
        if ($type === '') {
            $type_map = [
                '인물' => '인물', 'Person' => '인물', '장소' => '장소', 'Place' => '장소',
                '사건' => '사건', 'Event' => '사건', '기관' => '기관', 'Organization' => '기관',
                '사료' => '사료', '문건' => '사료', 'Document' => '사료',
            ];
            foreach ($labels_list as $label_name) {
                if (isset($type_map[$label_name])) { $type = $type_map[$label_name]; break; }
            }
        }

        $map[$nid] = [
            "id" => $nid,
            "label" => $icon . mb_substr((string)$label_text, 0, 20),
            "raw_id" => (string)$raw_id,
            "labels" => $labels_list,
            "type" => $type,
            "props" => $props,
            "aliases" => [],
            "color" => ["background" => $color, "border" => $color, "highlight" => ["background" => $color, "border" => "#333"]],
            "shape" => "box",
            "font" => ["color" => "#000", "size" => 14, "multi" => true],
            "borderWidth" => 2,
            "shadow" => true
        ];
    }
    return $nid;
}

// ── 관계 라벨 ──
function _get_rel_label($rtype) {
    $ko = [
        "P14_carried_out_by" => "수행(참여)", "P7_took_place_at" => "발생 장소",
        "ACTIVATED_AT" => "활동지", "P152_has_parent" => "가족 관계",
        "foaf:knows" => "동지/지인", "foaf:member" => "소속 기구",
        "P11_had_participant" => "참여 인물", "P108_has_produced" => "생성/저작",
        "P102_has_title" => "명칭/제목", "소속" => "소속",
        "P74_has_current_or_former_residence" => "설치 장소", "P89_falls_within" => "상위 행정구역",
        "동일인물" => "동일인물", "sameAs" => "동일인물", "owl:sameAs" => "동일인물",
        "P9_consists_of" => "기록 내 언급", "P134_was_continued_by" => "다음 언급",
        "P70_documents" => "기록함"
    ];
    return $ko[$rtype] ?? $rtype;
}

// ── Evidence 중복 제거 추가 ──
function add_fact_evidence(&$evidences, $prefix, $hash_parts, $payload, $max_count = 30) {
    if (!is_array($evidences)) $evidences = [];
    if (count($evidences) >= $max_count) return false;
    $key = "{$prefix}-" . sha1(implode('|', array_map(fn($v) => (string)$v, $hash_parts)));
    if (isset($evidences[$key])) return false;
    $evidences[$key] = $payload;
    return true;
}

// ── 동일인물 노드 병합 ──
function merge_same_person_nodes(&$nodes, &$edges, &$evidences) {
    if (empty($nodes)) return;

    $merge_target = [];

    // 1. 엣지 기반 동일인물 감지
    foreach ($edges as $e) {
        $type = $e['type'] ?? '';
        if (in_array($type, ['동일인물', 'sameAs', 'owl:sameAs'], true)) {
            $from = $e['from']; $to = $e['to'];
            if (isset($nodes[$from], $nodes[$to]) && $from !== $to) {
                $raw1 = (string)($nodes[$from]['raw_id'] ?? '');
                $raw2 = (string)($nodes[$to]['raw_id'] ?? '');
                $is_hangul1 = (bool)preg_match('/[\x{AC00}-\x{D7A3}]/u', $raw1);
                $is_hangul2 = (bool)preg_match('/[\x{AC00}-\x{D7A3}]/u', $raw2);
                $is_hanja1 = (bool)preg_match('/[\x{4E00}-\x{9FFF}]/u', $raw1);
                $is_hanja2 = (bool)preg_match('/[\x{4E00}-\x{9FFF}]/u', $raw2);

                if ($is_hangul1 && !$is_hangul2 && $is_hanja2) { $canon = $from; $alias = $to; }
                elseif ($is_hangul2 && !$is_hangul1 && $is_hanja1) { $canon = $to; $alias = $from; }
                else { $canon = $to; $alias = $from; }
                $merge_target[$alias] = $canon;
            }
        }
    }

    // 2. 한글독음/한자 기반 동일인물 감지
    $person_nids = [];
    foreach ($nodes as $nid => $node) {
        $type = $node['type'] ?? '';
        $labels = $node['labels'] ?? [];
        if ($type === '인물' || in_array('인물', $labels, true) || in_array('Person', $labels, true)) {
            $person_nids[] = $nid;
        }
    }

    $count_p = count($person_nids);
    for ($i = 0; $i < $count_p; $i++) {
        for ($j = $i + 1; $j < $count_p; $j++) {
            $nid1 = $person_nids[$i]; $nid2 = $person_nids[$j];
            if (!isset($nodes[$nid1], $nodes[$nid2])) continue;
            $raw1 = trim((string)($nodes[$nid1]['raw_id'] ?? ''));
            $raw2 = trim((string)($nodes[$nid2]['raw_id'] ?? ''));
            $props1 = $nodes[$nid1]['props'] ?? []; $props2 = $nodes[$nid2]['props'] ?? [];
            $reading1 = trim((string)($props1['한글독음'] ?? $props1['한글명칭'] ?? ''));
            $reading2 = trim((string)($props2['한글독음'] ?? $props2['한글명칭'] ?? ''));
            $hanja1 = trim((string)($props1['한자'] ?? '')); $hanja2 = trim((string)($props2['한자'] ?? ''));

            $is_same = false; $canon = null; $alias = null;
            if ($reading1 !== '' && $reading1 === $raw2) { $is_same = true; $canon = $nid2; $alias = $nid1; }
            elseif ($reading2 !== '' && $reading2 === $raw1) { $is_same = true; $canon = $nid1; $alias = $nid2; }
            elseif ($hanja1 !== '' && $hanja1 === $raw2) { $is_same = true; $canon = $nid1; $alias = $nid2; }
            elseif ($hanja2 !== '' && $hanja2 === $raw1) { $is_same = true; $canon = $nid2; $alias = $nid1; }

            if ($is_same && $canon && $alias) $merge_target[$alias] = $canon;
        }
    }

    if (empty($merge_target)) return;

    // Transitive resolution
    foreach ($merge_target as $src => $dst) {
        $visited = [$src => true]; $curr = $dst;
        while (isset($merge_target[$curr]) && !isset($visited[$curr])) {
            $visited[$curr] = true; $curr = $merge_target[$curr];
        }
        $merge_target[$src] = $curr;
    }

    // Merge nodes
    foreach ($merge_target as $alias_nid => $canon_nid) {
        if (!isset($nodes[$alias_nid], $nodes[$canon_nid]) || $alias_nid === $canon_nid) continue;
        $alias_node = $nodes[$alias_nid]; $canon_node = &$nodes[$canon_nid];
        $alias_raw = (string)($alias_node['raw_id'] ?? '');
        $canon_raw = (string)($canon_node['raw_id'] ?? '');

        if (!isset($canon_node['aliases']) || !is_array($canon_node['aliases'])) $canon_node['aliases'] = [];
        if ($alias_raw !== '' && $alias_raw !== $canon_raw) $canon_node['aliases'][] = $alias_raw;
        if (!empty($alias_node['aliases'])) $canon_node['aliases'] = array_merge($canon_node['aliases'], $alias_node['aliases']);
        $canon_node['aliases'] = array_values(array_unique($canon_node['aliases']));

        // 라벨 포맷
        $icon = "👤\n";
        $hanja_sub = '';
        foreach ($canon_node['aliases'] as $al) {
            if (preg_match('/[\x{4E00}-\x{9FFF}]/u', $al)) { $hanja_sub = $al; break; }
        }
        if ($hanja_sub !== '') {
            $canon_node['label'] = $icon . mb_substr("{$canon_raw} ({$hanja_sub})", 0, 25);
        } elseif (!empty($canon_node['aliases'])) {
            $canon_node['label'] = $icon . mb_substr("{$canon_raw} ({$canon_node['aliases'][0]})", 0, 25);
        }

        if (!empty($alias_node['labels'])) $canon_node['labels'] = array_values(array_unique(array_merge($canon_node['labels'] ?? [], $alias_node['labels'])));
        if (isset($alias_node['props'])) $canon_node['props'] = array_merge($alias_node['props'], $canon_node['props'] ?? []);
        unset($nodes[$alias_nid]);
    }

    // Redirect edges
    $new_edges = [];
    foreach ($edges as $e) {
        $from = $merge_target[$e['from']] ?? $e['from'];
        $to = $merge_target[$e['to']] ?? $e['to'];
        if ($from === $to) continue;
        if (in_array($e['type'] ?? '', ['동일인물', 'sameAs', 'owl:sameAs'], true)) continue;
        $e['from'] = $from; $e['to'] = $to;
        $new_edges[] = $e;
    }
    $edges = $new_edges;
}

// ── Prefetch 대상 선정 ──
function dynamic_prefetch_ratio($node_count) {
    if ($node_count <= 10) return 0.40;
    if ($node_count >= 30) return 0.30;
    return 0.40 - (($node_count - 10) * (0.10 / 20.0));
}

function select_prefetch_names_by_degree($nodes, $edges) {
    $node_list = array_values($nodes);
    $node_count = count($node_list);
    if ($node_count === 0) return [];

    $degree = [];
    foreach ($node_list as $n) $degree[$n['id']] = 0;
    foreach ($edges as $e) {
        if (isset($degree[$e['from']])) $degree[$e['from']]++;
        if (isset($degree[$e['to']])) $degree[$e['to']]++;
    }

    usort($node_list, function ($a, $b) use ($degree) {
        $da = $degree[$a['id']] ?? 0; $db = $degree[$b['id']] ?? 0;
        return $da === $db ? strcmp((string)($a['raw_id'] ?? $a['id']), (string)($b['raw_id'] ?? $b['id'])) : $db <=> $da;
    });

    $ratio = dynamic_prefetch_ratio($node_count);
    $target = clamp_int((int)round($node_count * $ratio), 5, 15);
    $target = min($node_count, $target);

    $names = [];
    foreach ($node_list as $n) {
        $raw = trim((string)($n['raw_id'] ?? ''));
        if ($raw === '' || isset($names[$raw])) continue;
        $names[$raw] = true;
        if (!empty($n['aliases'])) {
            foreach ($n['aliases'] as $alias) {
                $alias = trim((string)$alias);
                if ($alias !== '') $names[$alias] = true;
            }
        }
        if (count($names) >= $target) break;
    }
    return array_keys($names);
}

// ── Evidence Context 구축 (예산 제한) ──
/**
 * 근거 한 덩어리를 문자 예산 안에서 만든다: [그래프 사실표] → 기록 블록.
 *
 * 그래프 사실표는 $graph_cap까지(줄 단위로 자름), 나머지 예산은 기록이 순서대로 채운다. 기록은 하이브리드
 * 기준 시스템과 같은 형식(<SOURCE_EVIDENCE id="테이블-rowid">본문</SOURCE_EVIDENCE>)으로 넣어, 두 시스템이
 * 같은 기록을 같은 모양으로 받게 한다. 예산을 넘는 마지막 기록은 본문만 잘라 식별자를 지킨다.
 *
 * @param string   $graph_block  docent_graph_facts()의 결과 (없으면 '')
 * @param string[] $record_lines "[public.테이블] node=… rowid=N :: 본문" 형식의 줄 (docent_retrieve_records, docent_pg_row_text)
 * @return array ['context' => string, 'graph_chars' => int]
 */
function docent_assemble_context(string $graph_block, array $record_lines, int $total_budget, int $graph_cap): array {
    $total_budget = max(2000, $total_budget);
    $graph = '';
    foreach (preg_split('/\n/u', trim($graph_block)) as $line) {
        if ($line === '') continue;
        $next = $graph === '' ? $line : "{$graph}\n{$line}";
        if (mb_strlen($next) > min($graph_cap, $total_budget)) break;
        $graph = $next;
    }
    $parts = $graph !== '' ? [$graph] : [];
    $used = mb_strlen($graph);
    $seen = [];
    foreach ($record_lines as $line) {
        $line = normalize_whitespace_text((string)$line);
        if (!preg_match('/^\[[^\].]*\.(raw_[a-z_]+)\]\s+node=\S*\s+rowid=(\d+)\s+::\s*(.*)$/u', $line, $m)) continue;
        $id = "{$m[1]}-{$m[2]}";
        $body = trim($m[3]);
        if ($body === '' || isset($seen["{$id}|{$body}"])) continue;
        $seen["{$id}|{$body}"] = true;
        $body = mb_substr($body, 0, 600);
        $open = "<SOURCE_EVIDENCE id=\"{$id}\">\n";
        $close = "\n</SOURCE_EVIDENCE>";
        $sep = $parts ? 2 : 0;
        $room = $total_budget - $used - $sep - mb_strlen($open) - mb_strlen($close);
        if ($room < 120) break;
        $block = $open . mb_substr($body, 0, $room) . $close;
        $parts[] = $block;
        $used += $sep + mb_strlen($block);
    }
    return ['context' => implode("\n\n", $parts), 'graph_chars' => mb_strlen($graph)];
}

/** Evidence blocks in their actual prompt order, including any unwrapped narrative text. */
function docent_context_items($context) {
    $items = preg_split('/(<SOURCE_EVIDENCE\b[^>]*>[\s\S]*?<\/SOURCE_EVIDENCE>)/u',
        $context, -1, PREG_SPLIT_DELIM_CAPTURE | PREG_SPLIT_NO_EMPTY);
    return array_values(array_filter(array_map('trim', $items ?: []), static fn($s) => $s !== ''));
}

/**
 * 현대 표기와 사료 표기 사이의 지명·단체명 동의어 사전 (includes/historical_synonyms.json).
 * 표제어는 개발 세트의 엄선 문항에서 나왔다: 그 세트의 점수는 개발 결과로만 보고한다.
 */
function get_historical_synonyms(): array {
    static $dict = null;
    if ($dict === null) {
        $data = json_decode((string)file_get_contents(__DIR__ . '/historical_synonyms.json'), true);
        $dict = is_array($data['synonyms'] ?? null) ? $data['synonyms'] : [];
    }
    return $dict;
}

/**
 * 입력 문자열 또는 키워드 목록에서 역사적 동의어를 찾아 확장된 목록 반환
 */
function expand_historical_synonyms($input): array {
    $dict = get_historical_synonyms();
    $expanded = [];

    $text = is_array($input) ? implode(' ', $input) : (string)$input;
    foreach ($dict as $canon => $syns) {
        if (mb_strpos($text, $canon) !== false) {
            $expanded[] = $canon;
            foreach ($syns as $s) {
                $expanded[] = $s;
            }
        }
    }

    if (is_array($input)) {
        foreach ($input as $item) {
            $expanded[] = (string)$item;
        }
    }

    return array_values(array_unique(array_filter($expanded, fn($k) => trim($k) !== '')));
}
