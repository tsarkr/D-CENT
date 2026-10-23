<?php
/**
 * includes/database.php — Neo4j / PostgreSQL 접속 및 데이터 조회
 */

use Laudis\Neo4j\ClientBuilder;
use Laudis\Neo4j\Authentication\Authenticate;

// ── Neo4j 클라이언트 ──
function get_neo4j() {
    static $client = null;
    if ($client !== null) return $client;
    $client = ClientBuilder::create()
        ->withDriver('default', get_cfg('NEO4J_URI', 'bolt://127.0.0.1:7687'), Authenticate::basic(get_cfg('NEO4J_USER', 'neo4j'), get_cfg('NEO4J_PASSWORD')))
        ->build();
    return $client;
}

// ── PostgreSQL 클라이언트 ──
function get_pg() {
    static $pdo = null;
    static $tried = false;
    if ($pdo !== null) return $pdo;
    if ($tried) return null;
    $tried = true;
    try {
        $host = get_cfg('PG_HOST', '127.0.0.1');
        $port = get_cfg('PG_PORT', 5432);
        $dbname = get_cfg('PG_DATABASE', 'historical');
        $user = get_cfg('PG_USER', 'postgres');
        $pass = get_cfg('PG_PASSWORD', '');
        if (empty($pass)) return null;
        $dsn = "pgsql:host={$host};port={$port};dbname={$dbname}";
        $pdo = new PDO($dsn, $user, $pass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
        return $pdo;
    } catch (Exception $e) {
        return null;
    }
}

// ── PG 테이블 메타데이터 캐시 ──
function get_pg_tables_metadata($pdo) {
    $cache_dir = realpath(__DIR__ . '/..');
    $cache_file = ($cache_dir !== false ? $cache_dir : dirname(__DIR__)) . '/.pg_meta_cache.json';
    $cache_ttl = 60 * 60 * 24;

    if (is_file($cache_file) && is_readable($cache_file)) {
        $cache_raw = @file_get_contents($cache_file);
        if (is_string($cache_raw) && $cache_raw !== '') {
            $cache = json_decode($cache_raw, true);
            if (is_array($cache) && isset($cache['cached_at'], $cache['meta'])
                && (time() - (int)$cache['cached_at'] < $cache_ttl) && is_array($cache['meta'])) {
                return $cache['meta'];
            }
        }
    }

    $stmt = $pdo->query("
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.ordinal_position
        FROM information_schema.columns c
        INNER JOIN (
            SELECT DISTINCT table_schema, table_name
            FROM information_schema.columns
            WHERE column_name = 'tei' AND table_schema NOT IN ('pg_catalog', 'information_schema')
        ) t USING (table_schema, table_name)
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY c.table_schema, c.table_name, c.ordinal_position ASC
    ");
    $all_cols = $stmt->fetchAll(PDO::FETCH_ASSOC);

    $tables_raw = [];
    foreach ($all_cols as $row) {
        $schema = (string)($row['table_schema'] ?? '');
        $table = (string)($row['table_name'] ?? '');
        $col_name = (string)($row['column_name'] ?? '');
        $data_type = (string)($row['data_type'] ?? '');

        if (!preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $schema) || !preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $table)) continue;
        $key = "{$schema}.{$table}";
        if (!isset($tables_raw[$key])) {
            $tables_raw[$key] = ['schema' => $schema, 'table' => $table, 'first_col' => null, 'has_rowid' => false, 'text_cols' => []];
        }
        if ($col_name === 'rowid') $tables_raw[$key]['has_rowid'] = true;
        if ($tables_raw[$key]['first_col'] === null) {
            $tables_raw[$key]['first_col'] = $col_name;
        }
        if (in_array($data_type, ['character varying', 'text', 'character'], true)) {
            if (preg_match('/^[\p{L}_][\p{L}\p{N}_]*$/u', $col_name)) {
                $tables_raw[$key]['text_cols'][] = $col_name;
            }
        }
    }

    $meta = [];
    foreach ($tables_raw as $entry) {
        // Records are cited as "<table>-<rowid>"; use the rowid column wherever it exists,
        // whatever the column order of the table.
        $id_col = !empty($entry['has_rowid']) ? 'rowid' : ($entry['first_col'] ?? 'rowid');
        if (!preg_match('/^[\p{L}_][\p{L}\p{N}_]*$/u', $id_col)) $id_col = 'rowid';
        $text_cols = $entry['text_cols'];
        foreach (['명칭', '사건명', '제목'] as $p) {
            if (($idx = array_search($p, $text_cols)) !== false) {
                array_splice($text_cols, $idx, 1);
                array_unshift($text_cols, $p);
            }
        }
        $meta[] = ['schema' => $entry['schema'], 'table' => $entry['table'], 'id_col' => $id_col, 'text_cols' => array_slice($text_cols, 0, 6)];
    }

    $cache_payload = json_encode(['cached_at' => time(), 'meta' => $meta], JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_IGNORE);
    if ($cache_payload !== false) {
        @file_put_contents($cache_file, $cache_payload, LOCK_EX);
    }
    return $meta;
}

// ── PG row 조회 ──
function resolve_pg_row_node_id($row, $id_col, $search_cols, $names) {
    $id_value = isset($row[$id_col]) ? (string)$row[$id_col] : '';
    foreach ($names as $name) {
        if ($id_value !== '' && $id_value === (string)$name) return (string)$name;
    }
    $haystack_parts = [];
    foreach ($search_cols as $col) {
        if (isset($row[$col]) && $row[$col] !== null) $haystack_parts[] = (string)$row[$col];
    }
    $haystack = mb_strtolower(implode(' ', $haystack_parts));
    foreach ($names as $name) {
        $needle = mb_strtolower((string)$name);
        if ($needle !== '' && mb_strpos($haystack, $needle) !== false) return (string)$name;
    }
    return $names[0] ?? '';
}

// Record tables searched for evidence. Gazetteer and reference tables (administrative
// regions, repression organizations, event-place links) are reached through the graph
// and are not searched by name here.
const DOCENT_RECORD_TABLES = ['raw_bibliography', 'raw_source_info', 'raw_event_info', 'raw_detail_place'];

// Identifier of each record in the March First Movement Database (pipeline.config.SOURCE_ID_COLUMNS).
// Citations use the internal "<table>-<rowid>" key; these identifiers are what a reader can look up.
const DOCENT_SOURCE_ID_COLUMNS = [
    'raw_event_info' => '아이디',
    'raw_bibliography' => '문서아이디',
    'raw_source_info' => '출처정보아이디',
    'raw_detail_place' => '세부장소아이디',
];
const DOCENT_SOURCE_LABELS = [
    'raw_event_info' => '사건정보',
    'raw_bibliography' => '서지정보',
    'raw_source_info' => '출처정보',
    'raw_detail_place' => '세부장소',
];

function docent_record_source_id($table, $row) {
    $col = DOCENT_SOURCE_ID_COLUMNS[$table] ?? null;
    return ($col !== null && isset($row[$col])) ? trim((string)$row[$col]) : '';
}

function fetch_pg_rows_for_names($pdo, $names, $tables_meta) {
    $names = array_values(array_filter(array_unique(array_map(fn($n) => trim((string)$n), (array)$names))));
    if (empty($names)) return [];
    // Substring search only for names of three or more characters: a trigram index
    // cannot serve a two-character pattern. Shorter names still match by id.
    $long_names = array_values(array_filter($names, fn($n) => mb_strlen($n) >= 3));

    $out = [];
    foreach ($tables_meta as $m) {
        $schema = docent_valid_identifier($m['schema'] ?? '', 'public');
        $table = docent_valid_identifier($m['table'] ?? '', '');
        $id_col = docent_valid_identifier($m['id_col'] ?? '', 'id');
        if ($table === '' || !in_array($table, DOCENT_RECORD_TABLES, true)) continue;

        $fq = ($schema !== '' && $schema !== 'public') ? "\"{$schema}\".\"{$table}\"" : "\"{$table}\"";
        // The TEI of a row contains the text of every column, so tei alone covers the
        // text columns. The id match and the tei search are separate branches of a
        // UNION ALL: in one WHERE clause the cast on the id column made the planner
        // scan the whole table instead of using the trigram index on tei.
        $search_cols = [$id_col, 'tei'];
        $params = [];
        $id_placeholders = implode(',', array_fill(0, count($names), '?'));
        $branches = ["(SELECT * FROM {$fq} WHERE \"{$id_col}\"::text IN ({$id_placeholders}) LIMIT 25)"];
        foreach ($names as $name) $params[] = $name;
        if (!empty($long_names)) {
            $likes = implode(' OR ', array_fill(0, count($long_names), 'tei ILIKE ?'));
            $branches[] = "(SELECT * FROM {$fq} WHERE {$likes} LIMIT 25)";
            foreach ($long_names as $name) $params[] = "%{$name}%";
        }
        $q = implode(' UNION ALL ', $branches);

        try {
            $stmt = $pdo->prepare($q);
            $stmt->execute($params);
            $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
            $seen = [];
            foreach ($rows as $row) {
                $rid = (string)($row['rowid'] ?? $row[$id_col] ?? '');
                if ($rid !== '' && isset($seen[$rid])) continue;
                $seen[$rid] = true;
                if (count($seen) > 25) break;
                $snippets = [];
                foreach ($row as $k => $v) if ($k !== $id_col && $k !== 'tei') $snippets[$k] = $v !== null ? strval($v) : '';
                $out[] = [
                    'table' => $table, 'schema' => $schema,
                    'rowid' => $row[$id_col] ?? null, 'id_col' => $id_col,
                    'source_id' => docent_record_source_id($table, $row),
                    'match_cols' => $search_cols, 'tei' => $row['tei'] ?? null,
                    'snippets' => $snippets,
                    'node_id' => resolve_pg_row_node_id($row, $id_col, $search_cols, $names)
                ];
            }
        } catch (Exception $e) { error_log('pg record search failed for ' . $table . ': ' . $e->getMessage()); continue; }
    }
    return $out;
}

/**
 * Fetch the records that document nodes point to (P70_documents → 원천테이블, 원천rowid).
 * This is the primary, identifier-based record lookup (design principle P1);
 * fetch_pg_rows_for_names() is the name-based fallback.
 */
function fetch_pg_rows_for_refs($pdo, $refs, $tables_meta) {
    $by_table = [];
    foreach ((array)$refs as $ref) {
        $table = (string)($ref['table'] ?? '');
        $rowid = (int)($ref['rowid'] ?? 0);
        if ($rowid > 0 && in_array($table, DOCENT_RECORD_TABLES, true)) {
            $by_table[$table][$rowid] = (string)($ref['node_id'] ?? '');
        }
    }
    $id_cols = [];
    foreach ($tables_meta as $m) $id_cols[$m['table'] ?? ''] = docent_valid_identifier($m['id_col'] ?? '', 'rowid');
    $out = [];
    foreach ($by_table as $table => $rowids) {
        $id_col = $id_cols[$table] ?? 'rowid';
        $placeholders = implode(',', array_fill(0, count($rowids), '?'));
        try {
            $stmt = $pdo->prepare("SELECT * FROM \"{$table}\" WHERE rowid IN ({$placeholders}) LIMIT 25");
            $stmt->execute(array_keys($rowids));
            foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
                $snippets = [];
                foreach ($row as $k => $v) if ($k !== $id_col && $k !== 'tei') $snippets[$k] = $v !== null ? strval($v) : '';
                $out[] = [
                    'table' => $table, 'schema' => 'public',
                    'rowid' => $row[$id_col] ?? null, 'id_col' => $id_col,
                    'source_id' => docent_record_source_id($table, $row),
                    'match_cols' => ['rowid'], 'tei' => $row['tei'] ?? null,
                    'snippets' => $snippets,
                    'node_id' => $rowids[(int)($row['rowid'] ?? 0)] ?? '',
                    'via' => 'document',
                ];
            }
        } catch (Exception $e) { error_log('pg document lookup failed for ' . $table . ': ' . $e->getMessage()); }
    }
    return $out;
}

function fetch_pg_rows_for_name($pdo, $name, $tables_meta) {
    return fetch_pg_rows_for_names($pdo, [$name], $tables_meta);
}
