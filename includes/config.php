<?php
/**
 * includes/config.php — 환경변수, 세션, 기본 유틸리티
 */

require_once __DIR__ . '/../vendor/autoload.php';

use Dotenv\Dotenv;

// ── Session 초기화 ──
if (session_status() === PHP_SESSION_NONE) {
    $forwarded_proto = trim(explode(',', (string)($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? ''))[0]);
    $is_https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || strtolower($forwarded_proto) === 'https';
    ini_set('session.use_strict_mode', '1');
    session_cache_limiter('nocache');
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'httponly' => true,
        'secure' => $is_https,
        'samesite' => $is_https ? 'None' : 'Lax'
    ]);
    @session_start();
}

header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

// ── 환경변수 로드 ──
if (file_exists(__DIR__ . '/../.env')) {
    $dotenv = Dotenv::createImmutable(__DIR__ . '/..');
    $dotenv->load();
} elseif (file_exists(__DIR__ . '/../.streamlit/secrets.toml')) {
    $secrets_content = file_get_contents(__DIR__ . '/../.streamlit/secrets.toml');
    if ($secrets_content !== false) {
        if (preg_match_all('/^\s*([A-Za-z0-9_]+)\s*=\s*["\'](.+?)["\']\\s*$/m', $secrets_content, $matches, PREG_SET_ORDER)) {
            foreach ($matches as $m) {
                $k = $m[1]; $v = $m[2];
                if (getenv($k) === false) {
                    putenv("{$k}={$v}");
                    $_ENV[$k] = $v;
                }
            }
        }
    }
}

// ── 설정값 접근 ──
function get_cfg($key, $default = '') {
    $val = getenv($key);
    if ($val !== false) return trim($val, " \t\n\r\0\x0B\"'");
    if (isset($_ENV[$key])) return trim($_ENV[$key], " \t\n\r\0\x0B\"'");
    if (isset($_SERVER[$key])) return trim($_SERVER[$key], " \t\n\r\0\x0B\"'");
    return $default;
}

// ── 포커스 추론 ──
function infer_focus($term) {
    $text = trim((string)$term);
    if ($text === '') {
        return '인물';
    }

    $normalized = strtolower($text);
    $markers = [
        '인물' => ['인물','사람','인명','주인공','영웅','독립운동가','지도자','장군','장수','leader','person','hero','revolutionary','activist','politician','general'],
        '사건' => ['사건','시위','운동','봉기','폭동','재판','전쟁','전투','혁명','incident','event','protest','movement','uprising','trial','war','battle'],
        '장소' => ['장소','지역','도시','마을','곳','서울','부산','대구','인천','광주','대전','울산','경기','강원','충청','전라','경상','제주','만주','한양','평양','place','city','town','region','location','province','capital'],
        '기관' => ['기관','단체','정부','청','학교','협회','조직','군대','경찰','헌병','academy','company','organization','government','police','army','military','association'],
    ];

    foreach ($markers as $label => $words) {
        foreach ($words as $w) {
            if (strpos($normalized, $w) !== false) {
                return $label;
            }
        }
    }

    if (preg_match('/^[\p{Hangul}]{2,4}$/u', $text) || preg_match('/^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*$/', $text)) {
        return '인물';
    }
    if (preg_match('/\b(운동|시위|사건|전쟁|봉기|재판)\b/u', $text)) {
        return '사건';
    }
    if (preg_match('/\b(서울|부산|대구|인천|광주|대전|울산|경기|강원|충청|전라|경상|제주|만주|한양|평양)\b/u', $text)) {
        return '장소';
    }

    return '인물';
}

function normalize_focus($focus) {
    $value = trim((string)($focus ?? ''));
    if ($value === '') {
        return '인물';
    }

    $map = [
        'person' => '인물', 'people' => '인물', '인물' => '인물',
        'event' => '사건', '사건' => '사건',
        'place' => '장소', '장소' => '장소',
        'organization' => '기관', 'org' => '기관', '기관' => '기관',
        '문헌' => '문헌', '종합' => '종합',
    ];

    $normalized = strtolower($value);
    return $map[$normalized] ?? '인물';
}
