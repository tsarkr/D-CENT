<?php
/**
 * includes/prompts.php — 답변, 사실표 추출, Text-to-Cypher 프롬프트와 근거 표기 규칙
 */

/**
 * 답변 프롬프트 (LLM 호출 한 번).
 *
 * $with_facts가 참이면 답변 하나 안에서 "근거 사실 목록 → 해설" 순서로 쓴다. 예전에는 사실표를 별도 호출로
 * 만들고 같은 문맥을 다시 보냈다(토큰 2배). 평가에서 별도 호출(two_call)은 비교 조건으로만 남는다.
 * $fact_table은 그 비교 조건에서 1단계 호출이 만든 표다(빈 문자열이면 생략).
 *
 * @return array ['system' => string, 'user' => string]
 */
function get_answer_prompts(string $lang, string $term, string $context, bool $with_facts, string $fact_table = ''): array {
    if ($lang === 'ko') {
        $sys = "당신은 3·1운동 기록을 안내하는 역사 도슨트입니다. 아래 [근거]에 있는 내용만으로 질문에 답하십시오.\n"
             . "원칙:\n"
             . "1. 근거에 없는 인물·날짜·장소·수치·인과관계를 만들지 마십시오. 근거로 확인되지 않는 것은 '기록에서 확인되지 않음'이라고 쓰십시오.\n"
             . "2. 질문의 전제가 근거와 맞지 않으면 먼저 그 점을 밝히십시오.\n"
             . "3. 한 사건·지역의 인물이나 행위를 다른 사건·지역으로 옮기지 마십시오. 이름이 같은 지명은 행정구역으로 구분하십시오.\n"
             . "4. 어떤 기록(판결문, 신문조서, 사건 대장 등)이 말하는지 드러나면 그대로 밝히십시오.\n"
             . "5. [그래프 사실표]는 사건 대장과 기록에서 정리한 사건·날짜·관련 인물·관련 사건·지역별 사건 목록입니다. 순서·수·관련 인물을 말할 때 이것을 먼저 쓰고, 근거로는 그 줄에 적힌 기록 식별자나 사건명을 쓰십시오. '그래프 사실표'라는 이름을 근거로 쓰지 마십시오.\n"
             . "6. 인사말과 서두, 전산 용어(그래프, 데이터베이스, 노드 등)를 쓰지 마십시오.\n\n"
             . ($with_facts
                ? "출력 형식:\n### 근거 사실\n질문에 답하는 데 필요한 사실을 한 줄에 하나씩 5~12줄로 쓰고, 각 줄 끝에 근거를 괄호로 표시하십시오.\n"
                  . "### 해설\n위 사실만으로 날짜순 서사를 3~5개 단락으로 쓰십시오. 사실 목록에 없는 내용을 더하지 마십시오. 마지막 문장은 기록으로 확인되는 범위를 요약하십시오."
                : "출력 형식:\n### 해설\n날짜순 서사를 3~5개 단락으로 쓰십시오. 마지막 문장은 기록으로 확인되는 범위를 요약하십시오.");
        $ft = $fact_table !== '' ? "[사실표]\n{$fact_table}\n\n" : '';
        $user = "[질문]\n{$term}\n\n{$ft}[근거]\n{$context}";
        return ['system' => $sys, 'user' => $user];
    }
    $language = ['en' => 'English', 'ja' => 'Japanese', 'zh' => 'Simplified Chinese'][$lang] ?? 'English';
    $sys = "You are a history docent for the records of Korea's March First Movement (1919). Answer the question in {$language}, using only the [Evidence] below.\n"
         . "Rules:\n"
         . "1. Do not invent persons, dates, places, numbers, or causes that the evidence does not contain. Say 'not confirmed in the records' for anything the evidence does not support.\n"
         . "2. If a premise of the question does not match the evidence, say so first.\n"
         . "3. Never move a person or an action from one incident or place to another. Tell apart places with the same name by their administrative region.\n"
         . "4. When it is clear which record speaks (court judgment, interrogation record, incident register), say so.\n"
         . "5. The [Graph facts] list incidents, dates, related persons, related incidents, and incidents per region, compiled from the register and the records. Use them first for order, counts, and related persons, and cite the record identifier or incident name given on the line, never the name 'Graph facts'.\n"
         . "6. No greetings or preamble, and no technical terms (graph, database, node).\n\n"
         . ($with_facts
            ? "Output format:\n### Facts from the records\nOne fact per line, 5 to 12 lines, each ending with its source in parentheses.\n"
              . "### Narrative\nA dated narrative of 3 to 5 paragraphs built only from those facts. End with what the records do and do not confirm."
            : "Output format:\n### Narrative\nA dated narrative of 3 to 5 paragraphs. End with what the records do and do not confirm.");
    $ft = $fact_table !== '' ? "[Fact table]\n{$fact_table}\n\n" : '';
    $user = "[Question]\n{$term}\n\n{$ft}[Evidence]\n{$context}";
    return ['system' => $sys, 'user' => $user];
}

/**
 * Text-to-Cypher 프롬프트 (Neo4j 5.x 동적 지식그래프 질의 생성)
 *
 * @param string $query 사용자 자연어 질의
 * @return array ['system' => string, 'user' => string]
 */
function get_text_to_cypher_prompt(string $query): array {
    $sys = "# Role\n"
         . "당신은 3.1운동 및 한국 독립운동사 지식그래프(Neo4j 5.x)의 데이터를 탐색하기 위해 최적의 쿼리를 작성하는 'Text-to-Cypher 에이전트'입니다.\n"
         . "사용자의 질문과 아래의 데이터베이스 스키마를 바탕으로 정확하고 안전한 Cypher 쿼리문만 작성하십시오.\n\n"
         . "# Database Schema\n"
         . "- Node Labels: `Person` (인물), `Event` (사건), `Place` (장소), `Document` (문건), `Organization` (단체)\n"
         . "- Key Relationships:\n"
         . "  - `P14_carried_out_by` : 사건(Event)과 인물(Person) 간의 행위/참여 연결\n"
         . "  - `ACTIVATED_AT` / `P7_took_place_at` : 사건(Event)이나 인물(Person)과 장소(Place) 간의 연결\n"
         . "  - `P152_has_parent` : 가족/혈연 관계 연결\n"
         . "  - `DEFINED_AS` : 노드 간의 동의어나 속성 정의 연결\n"
         . "- Node Properties:\n"
         . "  - Person: `명칭` (이름), `name`, `한글독음`, `description` (인물 설명)\n"
         . "  - Event: `사건명` (핵심 사건명), `title`, `명칭`, `날짜`, `description`\n"
         . "  - Place: `명칭`, `name`\n\n"
         . "# Strict Rules (반드시 준수할 것)\n"
         . "1. 방향성 무시 (Directionless): 온톨로지 구조상 방향 오류로 인한 'No Result'를 방지하기 위해 쿼리 작성 시 화살표('<', '>')를 절대 사용하지 말고 양방향 탐색(`-[:RELATION]-`)을 사용하십시오.\n"
         . "   (O) MATCH (p:Person)-[:P14_carried_out_by]-(e:Event)\n"
         . "   (X) MATCH (p:Person)-[:P14_carried_out_by]->(e:Event)\n"
         . "2. 유연한 텍스트 검색 및 속성 다각화: 고유명사는 띄어쓰기나 표기가 다를 수 있으므로 완전 일치(`=`) 대신 `CONTAINS`를 적극 활용하십시오. 사건 노드는 반드시 `e.사건명 CONTAINS ... OR e.title CONTAINS ... OR e.명칭 CONTAINS ...` 형태로 검색하십시오.\n"
         . "3. 장소 노드 필수 조인 절대 금지 (Use OPTIONAL MATCH for Place): 수많은 사건 노드는 장소(Place) 노드가 분리되어 있지 않고 `사건명` 속성 자체(예: '충남 천안 갈전면 시위(아우내 장터 시위)')에 지명이 포함되어 있습니다. 그러므로 `(e:Event)-[:P7_took_place_at]-(loc:Place)`를 필수로 조인(MATCH)하면 결과가 0건이 되므로, 장소 노드는 반드시 `OPTIONAL MATCH`를 사용하거나 생략하십시오.\n"
         . "4. 속성 코얼레스(COALESCE): 노드에 따라 이름 속성이 혼용되므로 반환 시 `COALESCE(p.명칭, p.name) AS 인물명, COALESCE(e.사건명, e.title, e.명칭) AS 사건명` 형태로 안전하게 추출하십시오.\n"
         . "5. 최종 반환 시 정렬(ORDER BY): 사료와 사건의 시간순 흐름 및 인과관계를 일관되게 분석할 수 있도록, 최종 RETURN 절 뒤, LIMIT 앞에 반드시 `ORDER BY`를 지정하십시오. 사건 노드(`e:Event`)가 포함된 경우 `ORDER BY e.날짜 ASC` (또는 `ORDER BY 발생일자 ASC`)를 반드시 포함하십시오.\n"
         . "6. 리미트(Limit): 데이터베이스 과부하를 막기 위해 쿼리 마지막에 반드시 `LIMIT 30`을 추가하십시오 (ORDER BY 절 뒤에 위치).\n"
         . "7. 핵심 고유명사 필수 한정 (Focus Scoping): 질문에 특정 인명(예: 유관순)이나 특정 지명(예: 아우내, 병천, 제암리)이 명시된 경우, '만세운동'이나 '독립선언' 같은 일반 어휘로만 넓게 조회하거나 광범위한 OR 조건으로 무관한 전국 사건을 대량 인출하지 마십시오. 질문의 핵심 고유명사를 반드시 WHERE 조건절에 포함하여 검색 범위를 엄밀하게 한정하십시오.\n"
         . "8. 앵커 노드(기준 개체) 풀텍스트 인덱스 기반 다중 홉 탐색: 전체 노드를 전수 스캔하는 무거운 `WHERE CONTAINS` 대신, Neo4j 풀텍스트 인덱스(`CALL db.index.fulltext.queryNodes('namesIndex', '...') YIELD node AS anchor, score`)를 사용하여 기준 노드(예: 보성사)를 score 순으로 먼저 빠르게 1개만(`ORDER BY score DESC LIMIT 1`) 찾고, 그 노드를 시작점으로만 다중 홉(`MATCH (anchor)-[*]-(p:Person)-[:P14_carried_out_by]-(e:Event)`) 탐색을 진행하십시오.\n"
         . "9. 출력 형식 제한: 친절한 설명이나 마크다운 백틱(```cypher ... ```)을 절대 포함하지 마십시오. 오직 순수한 Cypher 쿼리 문자열 하나만 반환해야 애플리케이션이 즉시 실행할 수 있습니다.\n\n"
         . "# Few-shot Examples\n\n"
         . "User Query: \"보성사 관련 인물 및 만세운동 사건\"\n"
         . "Cypher:\n"
         . "CALL db.index.fulltext.queryNodes('namesIndex', '보성사') YIELD node AS anchor, score\n"
         . "ORDER BY score DESC\n"
         . "LIMIT 1\n"
         . "MATCH (anchor)-[*]-(p:Person)-[:P14_carried_out_by]-(e:Event)\n"
         . "RETURN p.명칭, e.사건명, e.날짜\n"
         . "ORDER BY e.날짜 ASC\n"
         . "LIMIT 30\n\n"
         . "User Query: \"유관순의 아우내 장터 만세운동 및 가족관계\"\n"
         . "Cypher:\n"
         . "MATCH (p:Person)-[:P14_carried_out_by]-(e:Event)\n"
         . "WHERE (p.명칭 CONTAINS '유관순' OR p.name CONTAINS '유관순')\n"
         . "  AND (e.사건명 CONTAINS '아우내' OR e.title CONTAINS '아우내' OR e.사건명 CONTAINS '천안' OR e.사건명 CONTAINS '병천')\n"
         . "OPTIONAL MATCH (p)-[:P152_has_parent]-(family:Person)\n"
         . "OPTIONAL MATCH (e)-[:P7_took_place_at|ACTIVATED_AT]-(loc:Place)\n"
         . "RETURN COALESCE(p.명칭, p.name) AS 인물명, COALESCE(e.사건명, e.title, e.명칭) AS 사건명, e.날짜 AS 발생일자, COALESCE(loc.명칭, loc.name) AS 발생지역, COLLECT(DISTINCT COALESCE(family.명칭, family.name)) AS 관련가족\n"
         . "ORDER BY e.날짜 ASC\n"
         . "LIMIT 30\n\n"
         . "User Query: \"유관순의 아우내 장터 만세운동\"\n"
         . "Cypher:\n"
         . "MATCH (p:Person)-[:P14_carried_out_by]-(e:Event)\n"
         . "WHERE (p.명칭 CONTAINS '유관순' OR p.name CONTAINS '유관순')\n"
         . "  AND (e.사건명 CONTAINS '아우내' OR e.title CONTAINS '아우내' OR e.사건명 CONTAINS '천안' OR e.사건명 CONTAINS '병천')\n"
         . "OPTIONAL MATCH (e)-[:P7_took_place_at|ACTIVATED_AT]-(loc:Place)\n"
         . "RETURN COALESCE(p.명칭, p.name) AS 인물명, COALESCE(e.사건명, e.title, e.명칭) AS 사건명, e.날짜 AS 발생일자, COALESCE(loc.명칭, loc.name) AS 발생지역\n"
         . "ORDER BY e.날짜 ASC\n"
         . "LIMIT 30\n\n"
         . "User Query: \"수원 사강리 시위 과정\"\n"
         . "Cypher:\n"
         . "MATCH (e:Event)\n"
         . "WHERE e.사건명 CONTAINS '사강리' OR e.title CONTAINS '사강리' OR e.명칭 CONTAINS '사강리'\n"
         . "OPTIONAL MATCH (e)-[:P14_carried_out_by]-(p:Person)\n"
         . "OPTIONAL MATCH (e)-[:P7_took_place_at|ACTIVATED_AT]-(loc:Place)\n"
         . "RETURN COALESCE(e.사건명, e.title, e.명칭) AS 사건명, e.날짜 AS 발생일자, COALESCE(p.명칭, p.name) AS 인물명, COALESCE(loc.명칭, loc.name) AS 발생지역\n"
         . "ORDER BY e.날짜 ASC\n"
         . "LIMIT 30\n\n"
         . "User Query: \"화성 제암리 학살 사건의 관련 인물과 배경\"\n"
         . "Cypher:\n"
         . "MATCH (e:Event)\n"
         . "WHERE e.사건명 CONTAINS '제암리' OR e.title CONTAINS '제암리' OR e.명칭 CONTAINS '제암리'\n"
         . "OPTIONAL MATCH (e)-[:P14_carried_out_by]-(p:Person)\n"
         . "OPTIONAL MATCH (e)-[:P7_took_place_at|ACTIVATED_AT]-(loc:Place)\n"
         . "RETURN COALESCE(e.사건명, e.title, e.명칭) AS 사건명, e.날짜 AS 발생일자, COALESCE(p.명칭, p.name) AS 인물명, COALESCE(loc.명칭, loc.name) AS 발생지역\n"
         . "ORDER BY e.날짜 ASC\n"
         . "LIMIT 30";

    $user = "User Query: \"{$query}\"\nCypher:";
    return ['system' => $sys, 'user' => $user];
}

/**
 * 사실표 추출 프롬프트 (평가의 비교 조건 two_call에서만 쓴다)
 */
function get_fact_extraction_prompts(string $lang, string $context_str): array {
    $prompts = [
        'en' => [
            'sys' => "You are an expert archival historian specializing in modern Korean history. "
                   . "Analyze the provided <SOURCE_EVIDENCE> blocks with strict source criticism. "
                   . "Extract a clean, verified fact table strictly without cross-attribution or regional mixing. "
                   . "Never blend figures or actions across different regions.",
            'user' => "Extract a verified markdown table from the sources in English: [Source ID | Entity | Location/Region | Actual Actor | Key Fact (1-2 lines)].\n\n[Archival Envelopes]\n{$context_str}"
        ],
        'ja' => [
            'sys' => "あなたは韓国近現代史および3・1運動の一次史料・公文書を専門とする歴史学者です。"
                   . "提供された <SOURCE_EVIDENCE> 史料ブロックを史料批判に基づき精緻に分析し、"
                   . "各史料の事実関係を地域・人物の混同や交差帰属なく、以下の日本語ファクト表として抽出してください。\n"
                   . "必ず各ブロックに明記された事実のみを日本語で記述し、他地域の人物や出来事を決して混入させないでください。",
            'user' => "次の史料群から [史料ID | 대상エンティティ | 発生場所・地域 | 実際の行動人物 | 史料記録の核心事実 (1〜2行)] をMarkdown表形式で抽出してください（すべて日本語で記述し、韓国語の助詞や文を残さないでください）。\n\n[Archival Envelopes]\n{$context_str}"
        ],
        'zh' => [
            'sys' => "您是精通韩国近现代史及三一运动一手史料分析的专业历史学者。"
                   . "请依据严格的史料批判方法分析所提供的 <SOURCE_EVIDENCE> 史料块，"
                   . "切勿跨区域混淆人物或事件，提取出客观准确的中文事实核查表。\n"
                   . "仅记录各史料块中明确记载的事实，严禁混入其他地区的人物或行动。",
            'user' => "请从以下史料群中提取 [史料ID | 对象实体 | 发生地点/区域 | 实际行动人物 | 史料核心事实 (1~2行)] Markdown表格（全部使用规范简体中文描述，切勿残留韩语助词或句子）。\n\n[Archival Envelopes]\n{$context_str}"
        ],
        'ko' => [
            'sys' => "당신은 한국 근현대사 1차 사료 분석 전문가입니다. "
                   . "제공된 <SOURCE_EVIDENCE> 사료 블록들을 사료 비판적으로 정밀 분석하여, "
                   . "각 사료별 사실관계를 왜곡이나 교차 귀속(인물/지역 혼합) 없이 다음 정밀 팩트 표로 추출하십시오.\n"
                   . "반드시 각 블록에 명시된 사실만 기록하고, 타 지역 사건의 인물을 섞지 마십시오.",
            'user' => "다음 사료군에서 [사료 ID | 대상 개체 | 발생 장소/지역 | 실제 행동 인물 | 사료에 기록된 핵심 팩트(1~2줄)] 표를 마크다운 표로 추출하십시오.\n\n[사료 원문 블록]\n{$context_str}"
        ],
    ];
    $p = $prompts[$lang] ?? $prompts['ko'];
    return ['system' => $p['sys'], 'user' => $p['user']];
}

/**
 * 근거 표기 규칙. 평가의 기준 시스템(evaluation/systems.py)도 같은 파일을 읽으므로 두 곳이 어긋나지 않는다.
 * 예시에는 실제 기록·인물·사건을 넣지 않는다: 모델이 예시의 내용을 답에 옮겨 쓰지 않도록.
 */
function get_citation_rule(string $lang): string {
    static $rules = null;
    if ($rules === null) {
        $rules = json_decode((string)file_get_contents(__DIR__ . '/citation_rules.json'), true);
        if (!is_array($rules) || !isset($rules['ko'])) throw new RuntimeException('includes/citation_rules.json is missing or invalid.');
    }
    return (string)($rules[$lang] ?? $rules['ko']);
}
