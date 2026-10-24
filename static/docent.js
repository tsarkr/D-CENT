/**
 * static/docent.js — 3.1 운동 역사 도슨트 프론트엔드 모듈
 * 
 * - I18N Localization Dictionary
 * - Intent Router (FULLTEXT / VECTOR / GRAPH 전략 분류)
 * - State & DOM 관리
 * - CSRF 자동 발급 및 갱신
 * - Multilingual 실시간 감지 및 언어 전환
 * - vis.js 지식그래프 시각화 및 노드 상세 / PostgreSQL 원천 사료 연동
 * - Gemini SSE 스트리밍 해설 처리
 */

(function() {
    'use strict';

    /* ━━━ I18N Localization Dictionary ━━━ */
    const I18N = {
        ko: {
            pageTitle: "3.1 운동 역사 도슨트 — 지식그래프 기반 다국어 역사 해설 시스템",
            headerTitle: "3.1 운동 역사 도슨트",
            heroTitle: "1919년 3·1 운동 역사 도슨트",
            heroBadgeDesc: "한국어 · English · 日本語 · 中文 자유 질의 지원",
            heroSubtitle: "어떤 언어로 질문하셔도 인물·사건·장소의 1차 사료를 탐색하고 동일 언어로 학술 해설을 제공합니다",
            searchBtn: "탐색",
            reasoningInitial: "AI가 질의를 분석하고 있습니다...",
            sourcesTitle: "수집된 사료",
            graphTitle: "지식그래프",
            centerGraph: "가운데로",
            nodeDetailTitle: "선택된 노드 상세",
            docentTitle: "도슨트 해설",
            explainBtn: "✨ 해설 생성",
            explainPlaceholder: "검색어를 입력하면 지식그래프 탐색 후 AI 도슨트 해설이 생성됩니다.",
            systemLogTitle: "시스템 로그",
            footer: "제작: jo.gyungmin@gmail.com · AI API 비용은 개인 감당 · 적절한 사용 부탁드립니다",
            footerSourcesLabel: "원본 자료:",
            srcSamil: "국사편찬위원회 삼일운동 데이터베이스",
            srcI815: "독립기념관 한국독립운동인명사전",
            srcThesaurus: "국사편찬위원회 한국역사용어시소러스",
            legendPerson: "인물",
            legendEvent: "사건",
            legendPlace: "장소",
            legendOrg: "기관",
            legendDoc: "사료",
            reExplainLabel: "언어 전환:",
            langName: "한국어",
            langBadge: "🇰🇷 한국어 해설"
        },
        en: {
            pageTitle: "March 1st Movement Historical Docent — Multilingual Knowledge Graph RAG",
            headerTitle: "March 1st Movement Docent",
            heroTitle: "1919 March 1st Movement Docent",
            heroBadgeDesc: "Supports Korean, English, Japanese, and Chinese queries",
            heroSubtitle: "Ask in any language: AI explores primary historical records and provides authentic scholarly commentary.",
            searchBtn: "Search",
            reasoningInitial: "AI is analyzing your historical query...",
            sourcesTitle: "Archival Sources",
            graphTitle: "Knowledge Graph",
            centerGraph: "Recenter",
            nodeDetailTitle: "Selected Entity Details",
            docentTitle: "Docent Commentary",
            explainBtn: "✨ Generate Commentary",
            explainPlaceholder: "Enter a question above to retrieve archival records and generate an AI docent commentary.",
            systemLogTitle: "System Logs",
            footer: "Author: jo.gyungmin@gmail.com · Powered by Knowledge Graph RAG & Gemini AI",
            footerSourcesLabel: "Data sources:",
            srcSamil: "NIKH March First Movement Database",
            srcI815: "Independence Hall of Korea Biographical Dictionary",
            srcThesaurus: "NIKH Korean History Thesaurus",
            legendPerson: "Person",
            legendEvent: "Event",
            legendPlace: "Place",
            legendOrg: "Organization",
            legendDoc: "Source",
            reExplainLabel: "Translate to:",
            langName: "English",
            langBadge: "🇺🇸 English Commentary"
        },
        ja: {
            pageTitle: "3.1独立運動 歴史ドーセント — 知識グラフ基盤の多言語歴史解説システム",
            headerTitle: "3.1独立運動 歴史ドーセント",
            heroTitle: "1919年 3・1独立運動 歴史ドーセント",
            heroBadgeDesc: "韓国語・英語・日本語・中国語での自由な質問に対応",
            heroSubtitle: "どの言語で質問しても、一次史料を探索し同一言語で学術的な歴史解説をお届けします。",
            searchBtn: "検索",
            reasoningInitial: "AIが質問の歴史的意図を分析しています...",
            sourcesTitle: "収集された一次史料",
            graphTitle: "知識グラフ",
            centerGraph: "中央に戻す",
            nodeDetailTitle: "選択された項目の詳細",
            docentTitle: "ドーセント歴史解説",
            explainBtn: "✨ 解説を生成",
            explainPlaceholder: "質問を入力すると、知識グラフの探索後にAIドーセントによる学術解説が生成されます。",
            systemLogTitle: "システムログ",
            footer: "制作: jo.gyungmin@gmail.com · 知識グラフRAG＆Gemini AI搭載",
            footerSourcesLabel: "原資料:",
            srcSamil: "国史編纂委員会 三・一運動データベース",
            srcI815: "独立記念館 韓国独立運動人名辞典",
            srcThesaurus: "国史編纂委員会 韓国歴史用語シソーラス",
            legendPerson: "人物",
            legendEvent: "事件",
            legendPlace: "場所",
            legendOrg: "機関",
            legendDoc: "史料",
            reExplainLabel: "言語切り替え:",
            langName: "日本語",
            langBadge: "🇯🇵 日本語解説"
        },
        zh: {
            pageTitle: "三一运动 历史智能导览 — 基于知识图谱的多语言历史解说系统",
            headerTitle: "三一运动 历史智能导览",
            heroTitle: "1919年 三一运动 历史智能导览",
            heroBadgeDesc: "支持韩语、英语、日语、中文自然语言提问",
            heroSubtitle: "无论您以何种语言提问，系统均将探索一手史料并以相同语言提供学术级专业解说。",
            searchBtn: "探索",
            reasoningInitial: "AI正在深度解析您的历史查询意图...",
            sourcesTitle: "已检索一手史料",
            graphTitle: "知识图谱",
            centerGraph: "重置中心",
            nodeDetailTitle: "选定节点史料详情",
            docentTitle: "智能导览解说",
            explainBtn: "✨ 生成导览解说",
            explainPlaceholder: "输入查询内容后，系统将检索知识图谱并生成AI历史学者级导览解说。",
            systemLogTitle: "系统运行日志",
            footer: "制作: jo.gyungmin@gmail.com · 知识图谱RAG与AI技术驱动",
            footerSourcesLabel: "原始资料:",
            srcSamil: "国史编纂委员会 三一运动数据库",
            srcI815: "独立纪念馆 韩国独立运动人名辞典",
            srcThesaurus: "国史编纂委员会 韩国历史用语叙词表",
            legendPerson: "人物",
            legendEvent: "事件",
            legendPlace: "地点",
            legendOrg: "机构",
            legendDoc: "史料",
            reExplainLabel: "切换解说语言:",
            langName: "中文",
            langBadge: "🇨🇳 中文解说"
        }
    };

    /* ━━━ State ━━━ */
    let network = null;
    let lastEvidences = [];
    let lastNarrative = null;        // 서사 경로 결과 (explain에서 재사용)
    let narrativePromise = null;     // graph와 동시에 시작한 서사 경로 요청
    let pgPrefetchTexts = [];
    let recordSources = {};          // "<table>-<rowid>" -> identifier in the source database
    let currentNodes = [];
    let currentEdges = [];
    let analysisResult = null;
    let csrfToken = (typeof window !== 'undefined' && window.__PRELOADED_CSRF__) ? window.__PRELOADED_CSRF__ : '';
    let selectedUiLang = 'auto'; // 'auto' | 'ko' | 'en' | 'ja' | 'zh'
    let isSubmitting = false;
    // Progress messages and logs: the UI language picker decides when set; otherwise Korean for Korean queries, English for others.
    let stepKo = true;
    const tr = (ko, en) => (stepKo ? ko : en);

    /* ━━━ Backend endpoint ━━━ */
    const API_BASE = 'docent.php';

    /* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
     *  질의 유형 분류 — 클라이언트 사전 분류 모듈
     *  규칙 기반으로 질문을 개체 조회 / 서술형 / 관계·서사 탐색으로 나눕니다.
     *  검색 방식을 바꾸지는 않으며, 서버 질의 분석이 실패했을 때
     *  의도 유형의 기본값(SERVER_INTENT)으로만 쓰입니다.
     *  (벡터·임베딩 검색은 수행하지 않습니다.)
     * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

    const STRATEGY_LABELS = {
        FULLTEXT: { label: '개체 조회', en: 'Entity lookup', icon: '🔤', color: 'text-blue-600' },
        VECTOR:   { label: '배경·서술형 질의', en: 'Background / descriptive', icon: '📖', color: 'text-purple-600' },
        GRAPH:    { label: '관계·서사 탐색', en: 'Relation / narrative', icon: '🕸️', color: 'text-emerald-600' }
    };
    // 서버 질의 분석(handle_action_analyze)의 intent_type 체계로 변환
    const SERVER_INTENT = { FULLTEXT: 'ENTITY_SEARCH', VECTOR: 'GENERAL_INFO', GRAPH: 'EVENT_RELATION' };

    /* ── 패턴 사전 ── */
    const GRAPH_PATTERNS = [
        /(?:만세운동|만세시위|독립만세|만세|시위|사건|의거|거사)/,
        /(?:인쇄|배포|전파|전달|작성|서명)/,
        /(?:주도|관여|가담|참여|참가)/,
        /[가-힣]{2,6}의\s+(?:아우내|태화관|탑골공원|보성사|서대문형무소|독립선언|만세시위|만세운동|동지|제자|스승|가족|관계)/, // 관계 표현
        /(?:와|과)\s*(?:함께|같이|교류|연대|협력)/,
        /(?:연결|연관|관련|관계|연루|관계된|연관된)\s*(?:된|되는|사람|인물|단체)/,
        /(?:제자|스승|동지|동료|부하|상관|후배|선배)\s*(?:들|의|가|은|는)/,
        /(?:수감|투옥|수감된|구금)\s*(?:된|되었던|했던)?\s*(?:사람|인물|이)/,
        /(?:출신|졸업)\s*(?:들|자|인물)\s*(?:이|가|의|은|는)/,
        /(?:주도|이끌|참여|가담)\s*(?:한|했던)?\s*(?:다른|또\s*다른|이후)/,
        /(?:이후|이전|그\s*뒤|그\s*후)\s*(?:활동|행적|행보|경력)/,
        /다른\s*지역\s*(?:의|에서)?\s*(?:시위|운동|만세)/,
        /함께.*(?:수감|투옥|활동|참여|서명)/,
        /(?:설립|창설).*(?:출신|졸업).*(?:주도|참여|활동)/,
        /인물.*(?:찾|검색|나열|목록|알려)/,
        /(?:몇\s*명|누구누구|모두|전원|명단)/,
        /(?:who\s+(?:else|also|together))/i,
        /(?:connected|related|associated)\s+(?:to|with)/i,
        /(?:people|persons|figures)\s+(?:who|that|involved)/i,
        /(?:imprisoned|jailed|detained)\s+(?:with|alongside|together)/i,
        /(?:alumni|graduates|students)\s+(?:of|from)\s+.+\s+(?:led|organized|participated)/i,
        /(?:after|later|subsequent)\s+(?:activities|actions|movements)/i,
        /(?:と共に|とともに|と一緒に|と交流)/,
        /(?:関連|関係|連携|連帯)\s*(?:した|する|のある)/,
        /(?:出身者|卒業生)\s*(?:が|の|は)/,
        /(?:与|和|同).*(?:一起|共同|交流|关联)/,
        /(?:相关|关联|关系|联系).*(?:人物|人|组织)/
    ];

    const VECTOR_PATTERNS = [
        /(?:배경|원인|이유|결과|영향|의의|의미|특징|성격|사상|이념|노선)/,
        /(?:어떤|어떠한|무슨)\s*(?:영향|의미|특징|배경|역할)/,
        /(?:왜|어째서|어떻게)\s+.*(?:했|되었|나타|발생|일어)/,
        /(?:핵심|근본|본질|정신|취지|목적|의도)\s*(?:은|는|이|가|사상)/,
        /(?:비폭력|무장투쟁|자주독립|민주주의|민족자결)/,
        /(?:설명|묘사|서술|분석|해석|논의|비교|평가)\s*(?:해|하|해줘|해주|해주세요)/,
        /(?:전말|전개|경과|경위|과정)\s*(?:을|를|이|가)?\s*(?:알려|설명|서술)/,
        /(?:미친|끼친)\s*영향/,
        /(?:나타난|드러난|보여준)\s*(?:특징|양상|모습)/,
        /(?:통치|탄압|검거|고문|학살)\s*(?:가|이|의)\s*.*(?:영향|결과|의미)/,
        /(?:explain|describe|analyze|interpret|discuss|compare)/i,
        /(?:impact|influence|effect|significance|meaning|cause|reason|background)/i,
        /(?:why|how)\s+(?:did|was|were|could|would)/i,
        /(?:characteristics?|features?|nature|essence|spirit)\s+(?:of|in)/i,
        /(?:non[\s-]?violen|armed\s+struggle|self[\s-]?determination)/i,
        /(?:なぜ|どうして|どのように|いかに)/,
        /(?:背景|原因|結果|影響|意義|特徴|思想|精神)/,
        /(?:説明|分析|解説|解釈)/,
        /(?:为什么|怎么|如何|什么原因)/,
        /(?:背景|原因|结果|影响|意义|特征|思想|精神)/,
        /(?:解释|说明|分析|描述)/
    ];

    const FULLTEXT_PATTERNS = [
        /(?:누구|누가|누군)\s*(?:야|인가|입니까|예요|이에요)/,
        /(?:뭐야|뭔가|무엇인가|무엇이|무엇입니까)/,
        /(?:어디|어느\s*곳|어디에|위치)\s*(?:야|인가|에\s*있|입니까)/,
        /(?:직업|직함|본적|주소|신분|나이|생년|출생|고향|별명|이칭|호)\s*(?:이|가|은|는)?\s*(?:뭐|무엇|어떻|알려|\?|$)/,
        /(?:언제)\s*(?:태어|출생|사망|순국|체포|석방)/,
        /(?:^[가-힣]{2,6})\s*(?:이\s*누구|이\s*뭐|은\s*어디|는\s*어디|에\s*대해)/,
        /(?:^who\s+(?:is|was))\s+/i,
        /(?:^what\s+(?:is|was))\s+/i,
        /(?:^where\s+(?:is|was))\s+/i,
        /(?:occupation|birth|death|age|hometown)\s+(?:of|is)/i,
        /(?:は誰|って誰|とは何|はどこ|の職業|の出身)/,
        /(?:是谁|是什么|在哪里|什么人|什么地方|职业)/
    ];

    const RELATION_VERBS_KO = [
        '교류', '연대', '협력', '만난', '만났', '접촉', '소개', '추천',
        '파견', '보낸', '전달', '위임', '지시', '명령', '감화', '영입',
        '함께', '동행', '합류', '가담', '참가', '참석', '출석', '서명'
    ];

    const ATTRIBUTE_WORDS = [
        '직업', '직함', '본적', '주소', '신분', '나이', '생년월일', '출생지',
        '사망일', '별명', '이칭', '호', '자', '본관', '학력', '종교',
        '출신지', '고향', '위치', '소재지', '설립일', '창립일', '대표',
        'occupation', 'birth', 'death', 'hometown', 'location',
        '職業', '出身', '生年', '没年',
        '职业', '出生', '死亡', '籍贯'
    ];

    const KNOWN_ENTITIES = [
        '손병희','이종일','최린','최남선','한용운','이승훈','유관순','김구',
        '이동휘','안창호','이광수','박은식','김마리아','이희영','유중권','유중무','이소제',
        '조인원','김구응','홍일선',
        '박인호','오세창','권동진','이종훈','홍기조','나용환','이갑성',
        '길선주','양전백','이필주','김병조','백용성','정춘수','김창준',
        '신석구','오화영','임예환','홍병기','박희도','나인협','한규설',
        '이완용','하세가와','사이토','우가키',
        '보성사','태화관','탑골공원','서대문형무소','아우내','아우내 장터','아우내장터','병천','천안',
        '수원','제암리','사강리','화성','갈전면','평양','대구','광주','원산','의주',
        '파고다공원','독립문','경성','종로',
        '천도교','신한청년당','의열단','대한민국임시정부','기독교','불교',
        '조선총독부','헌병경찰',
        '독립선언서','독립선언','만세운동','만세시위','3·1운동','3.1운동','2·8독립선언','기미독립선언서'
    ];

    /**
     * classifyIntent(text) — 질의 의도 분류 라우터
     */
    function classifyIntent(text) {
        if (!text || typeof text !== 'string') {
            return { intent: 'FULLTEXT', keywords: [], reason: tr('빈 질의이므로 기본 전문 검색을 적용합니다.', 'Empty query; using default full-text search.') };
        }

        const q = text.trim();
        const extractedEntities = [];
        for (const ent of KNOWN_ENTITIES) {
            if (q.includes(ent)) extractedEntities.push(ent);
        }

        // 조사 및 특수문자 제거 후 2~6자 고유명사 토큰 추출
        const cleanTokens = q.split(/[\s,·\.\?!~]+/g)
            .map(t => t.replace(/(?:의|은|는|이|가|을|를|과|와|도|에서|에게|으로|로|에|란|이란)$/u, '').trim())
            .filter(t => t.length >= 2 && !['무엇', '어떤', '누구', '알려줘', '설명해줘', '있습니까', '있는가', '관련'].includes(t));

        const keywords = [...new Set([...extractedEntities, ...cleanTokens])].slice(0, 5);

        // ── [특수 규칙] 인물·사건의 이동(A에서 B로) 및 인과/전개 관계 감지 시 무조건 GRAPH 우선 분류 ──
        // 질문 끝에 '설명해 줘'나 '서술해 줘'가 있더라도, 내용상 인물·사건의 이동이나 인과관계를 묻는다면 무조건 GRAPH로 강제 우선 분류
        const hasTransitionOrCausality = (
            // 1. A에서 B로 이동/경로/전파/확산
            /(?:에서|부터)\s+.*(?:로|으로|까지|을\s*거쳐|를\s*거쳐|향해)/.test(q) ||
            /(?:이동|전파|확산|파급|피신|망명|진출|경유|거쳐|거점으로|파견|밀파|귀향|상경|하향|탈출|내려가|올라가|떠나)/.test(q) ||
            /(?:다른|타)\s*(?:지역|도시|지방|학교|단체|조직)/.test(q) ||
            /(?:경로|루트|행적|이동\s*과정|전달\s*과정|전파\s*과정|확산\s*과정)/.test(q) ||

            // 2. 인과관계 및 사건 연쇄 (A 이후 B, 계기로 이어짐 등)
            /(?:인과|인과관계|인과성|연쇄|도화선|발단|계기|영향을\s*받아|이어져|이어진|이어지는|계승|발전|발생\s*과정)/.test(q) ||
            /(?:이후|직후|그\s*뒤|그\s*후|후에)\s+.*(?:만세|시위|운동|사건|체포|결성|수립|조직|주도|참여|발발|전개|일어|퍼져)/.test(q) ||
            /(?:관여|인쇄|배포|주도|참여|가담|지휘|서명|체포|순국|투옥).*(?:이후|그\s*뒤|그\s*후|다른|거점|만세|시위|사건|지역|계기)/.test(q) ||
            /(?:로|으로)\s*(?:인해|말미암아|이어진|연결된|확대된|촉발된)/.test(q) ||
            /(?:전개|촉발|파급|확산)\s*(?:과정|경위|전말)/.test(q) ||

            // 3. 복수 개체 간 관계 또는 특정 인물+사건 참여/전개
            (extractedEntities.length >= 2) ||
            (extractedEntities.length >= 1 && /(?:만세운동|만세시위|독립만세|시위|의거|거사)/.test(q) && /(?:주도|참여|관여|가담|이끈|이끌었|체포|순국|과정|전개|경위)/.test(q))
        );

        // 이동/인과관계 질의는 질문 끝의 '설명해 줘' / '서술해 줘' 유무와 무관하게 무조건 GRAPH 우선 분류
        if (hasTransitionOrCausality) {
            return {
                intent: 'GRAPH',
                keywords: keywords.length > 0 ? keywords : [q],
                reason: buildReason('GRAPH', q, extractedEntities, true)
            };
        }

        let graphScore = 0;
        let vectorScore = 0;
        let fulltextScore = 0;

        for (const pat of GRAPH_PATTERNS) {
            if (pat.test(q)) { graphScore += 3; break; }
        }
        for (const pat of VECTOR_PATTERNS) {
            if (pat.test(q)) { vectorScore += 2; break; }
        }
        for (const pat of FULLTEXT_PATTERNS) {
            if (pat.test(q)) { fulltextScore += 3; break; }
        }

        // 복수 개체 감지 시 GRAPH 가산
        if (extractedEntities.length >= 2) graphScore += 3;

        for (const rv of RELATION_VERBS_KO) {
            if (q.includes(rv)) { graphScore += 2; break; }
        }

        let hasAttributeWord = false;
        for (const aw of ATTRIBUTE_WORDS) {
            if (q.toLowerCase().includes(aw.toLowerCase())) { hasAttributeWord = true; break; }
        }
        if (hasAttributeWord && extractedEntities.length <= 1) fulltextScore += 2;

        const qLen = q.replace(/\s/g, '').length;
        if (qLen <= 6 && extractedEntities.length <= 1) fulltextScore += 2;
        else if (qLen >= 30) vectorScore += 1;

        if (/[가-힣]+(?:와|과|하고)\s*[가-힣]+/.test(q) && extractedEntities.length >= 1) {
            graphScore += 2;
        }

        if (vectorScore > 0) {
            const deepContextWords = ['핵심','사상','의의','의미','배경','원인','영향','특징','정신','이념','노선','본질','취지'];
            for (const dcw of deepContextWords) {
                if (q.includes(dcw)) { vectorScore += 2; break; }
            }
        }

        let intent, reason;
        if (graphScore > vectorScore && graphScore > fulltextScore) {
            intent = 'GRAPH';
            reason = buildReason('GRAPH', q, extractedEntities, false);
        } else if (vectorScore > fulltextScore) {
            intent = 'VECTOR';
            reason = buildReason('VECTOR', q, extractedEntities, false);
        } else {
            intent = 'FULLTEXT';
            reason = buildReason('FULLTEXT', q, extractedEntities, false);
        }

        return { intent, keywords: keywords.length > 0 ? keywords : [q], reason };
    }

    function buildReason(intent, q, entities, isTransition = false) {
        const entStr = entities.length > 0 ? `[${entities.join(', ')}]` : '';
        switch (intent) {
            case 'GRAPH':
                if (isTransition || /(?:에서|부터)\s+.*(?:로|으로|까지)/.test(q) || /(?:이동|전파|확산|이후|다른\s*지역)/.test(q)) {
                    return tr(`인물·사건의 이동 경로 및 인과관계(A에서 B로) 추적이 필요한 다중 홉 관계 탐색 질의입니다.`,
                        `Multi-hop query tracing movement or cause and effect between people and events (A to B).`);
                }
                if (entities.length >= 2) return tr(`복수 개체${entStr} 간의 관계 추적 또는 다중 홉 탐색이 필요한 질의입니다.`,
                    `Query tracing relations among several entities${entStr} (multi-hop).`);
                return tr(`개체 간 연쇄적 관계 또는 연루자/인과 추적이 필요한 복합 추론 질의입니다.`,
                    `Query tracing chained relations, participants, or causes across entities.`);
            case 'VECTOR':
                return tr(`역사적 배경, 원인/결과, 사상/특징 등 문맥과 의미 해석이 필요한 서술형 질의입니다.`,
                    `Descriptive query about background, causes and effects, or ideas.`);
            case 'FULLTEXT':
                if (entities.length === 1) return tr(`특정 개체${entStr}의 단편적 사실/속성을 묻는 단순 조회 질의입니다.`,
                    `Simple lookup of facts about one entity${entStr}.`);
                return tr(`특정 고유명사의 기본 정보를 묻는 키워드 검색 질의입니다.`,
                    `Keyword lookup of basic facts about a proper noun.`);
        }
    }

    /* ━━━ DOM Elements ━━━ */
    const $ = id => document.getElementById(id);
    let $input, $heroSection, $reasoningPanel, $reasoningSteps, $reasoningTitle;
    let $sourcesPanel, $sourcesBadges, $sourcesCount, $graphSection;
    let $explanationSection, $explanationContent, $explainBtn;
    let $systemLogSection, $systemLogContent, $nodeInfoPanel, $nodeInfoContent;

    function initDomRefs() {
        $input              = $('search-input');
        $heroSection        = $('hero-section');
        $reasoningPanel     = $('reasoning-panel');
        $reasoningSteps     = $('reasoning-steps');
        $reasoningTitle     = $('reasoning-title');
        $sourcesPanel       = $('sources-panel');
        $sourcesBadges      = $('sources-badges');
        $sourcesCount       = $('sources-count');
        $graphSection       = $('graph-section');
        $explanationSection = $('explanation-section');
        $explanationContent = $('explanation-content');
        $explainBtn         = $('explain-btn');
        $systemLogSection   = $('system-log-section');
        $systemLogContent   = $('system-log-content');
        $nodeInfoPanel      = $('node-info-panel');
        $nodeInfoContent    = $('node-info-content');

        if ($input) {
            $input.addEventListener('keydown', e => { if (e.key === 'Enter') submitQuery(); });
            $input.addEventListener('input', updateInputLanguageBadge);
        }
    }

    /* ━━━ Multilingual UI Language Switcher ━━━ */
    function setUiLanguage(lang) {
        selectedUiLang = lang;
        if (lang !== 'auto') stepKo = lang === 'ko';
        document.querySelectorAll('.lang-nav-btn').forEach(btn => {
            if (btn.dataset.lang === lang) {
                btn.className = "lang-nav-btn px-2.5 py-1 rounded-lg text-xs font-semibold bg-white text-ink-900 shadow-xs transition-all";
            } else {
                btn.className = "lang-nav-btn px-2 py-1 rounded-lg text-xs text-ink-600 hover:text-ink-900 transition-all";
            }
        });

        const activeDict = I18N[lang === 'auto' ? 'ko' : lang] || I18N.ko;
        document.title = activeDict.pageTitle;
        if ($('header-title')) $('header-title').textContent = activeDict.headerTitle;
        if ($('hero-title')) $('hero-title').textContent = activeDict.heroTitle;
        if ($('hero-badge-desc')) $('hero-badge-desc').textContent = activeDict.heroBadgeDesc;
        if ($('hero-subtitle')) $('hero-subtitle').textContent = activeDict.heroSubtitle;
        if ($('search-btn-text')) $('search-btn-text').textContent = activeDict.searchBtn;
        if ($('sources-title')) $('sources-title').textContent = activeDict.sourcesTitle;
        if ($('graph-title')) $('graph-title').textContent = activeDict.graphTitle;
        if ($('center-graph-btn')) $('center-graph-btn').textContent = activeDict.centerGraph;
        if ($('node-detail-title')) $('node-detail-title').textContent = activeDict.nodeDetailTitle;
        if ($('docent-title')) $('docent-title').textContent = activeDict.docentTitle;
        if ($('explain-btn')) $('explain-btn').textContent = activeDict.explainBtn;
        if ($('system-log-title')) $('system-log-title').textContent = activeDict.systemLogTitle;
        if ($('reexplain-label')) $('reexplain-label').textContent = activeDict.reExplainLabel;
        if ($('footer-text')) $('footer-text').textContent = activeDict.footer;
        if ($('footer-sources-label')) $('footer-sources-label').textContent = activeDict.footerSourcesLabel;
        if ($('src-samil')) $('src-samil').textContent = activeDict.srcSamil;
        if ($('src-i815')) $('src-i815').textContent = activeDict.srcI815;
        if ($('src-thesaurus')) $('src-thesaurus').textContent = activeDict.srcThesaurus;

        if ($('legend-person')) $('legend-person').textContent = activeDict.legendPerson;
        if ($('legend-event')) $('legend-event').textContent = activeDict.legendEvent;
        if ($('legend-place')) $('legend-place').textContent = activeDict.legendPlace;
        if ($('legend-org')) $('legend-org').textContent = activeDict.legendOrg;
        if ($('legend-doc')) $('legend-doc').textContent = activeDict.legendDoc;

        updateInputLanguageBadge();
        logSystem(`${tr('UI 언어 전환', 'UI language')}: ${lang.toUpperCase()}`);
    }

    /* ━━━ Real-time Input Language Detection ━━━ */
    function detectInputLanguage(text) {
        if (!text || !text.trim()) return 'auto';
        if (/[\uac00-\ud7af\u1100-\u11ff]/.test(text)) return 'ko';
        if (/[\u3040-\u309f\u30a0-\u30ff]/.test(text)) return 'ja';
        if (/[\u4e00-\u9fff]/.test(text)) return 'zh';
        if (/[a-zA-Z]/.test(text)) return 'en';
        return 'auto';
    }

    function updateInputLanguageBadge() {
        if (!$input) return;
        const val = $input.value.trim();
        const tag = $('input-lang-tag');
        if (!tag) return;
        if (!val) {
            tag.className = "ml-3 px-2 py-0.5 text-[11px] font-mono font-semibold rounded-md bg-ink-100 text-ink-600 border border-ink-200/70 select-none whitespace-nowrap transition-colors";
            tag.textContent = selectedUiLang === 'auto' ? "🌐 ALL LANG" : `🌐 ${selectedUiLang.toUpperCase()}`;
            return;
        }
        const detected = detectInputLanguage(val);
        if (detected === 'ko') {
            tag.className = "ml-3 px-2 py-0.5 text-[11px] font-mono font-semibold rounded-md bg-blue-100 text-blue-800 border border-blue-200 select-none whitespace-nowrap transition-colors";
            tag.textContent = "🇰🇷 한국어";
        } else if (detected === 'ja') {
            tag.className = "ml-3 px-2 py-0.5 text-[11px] font-mono font-semibold rounded-md bg-amber-100 text-amber-800 border border-amber-200 select-none whitespace-nowrap transition-colors";
            tag.textContent = "🇯🇵 日本語";
        } else if (detected === 'zh') {
            tag.className = "ml-3 px-2 py-0.5 text-[11px] font-mono font-semibold rounded-md bg-emerald-100 text-emerald-800 border border-emerald-200 select-none whitespace-nowrap transition-colors";
            tag.textContent = "🇨🇳 中文";
        } else if (detected === 'en') {
            tag.className = "ml-3 px-2 py-0.5 text-[11px] font-mono font-semibold rounded-md bg-indigo-100 text-indigo-800 border border-indigo-200 select-none whitespace-nowrap transition-colors";
            tag.textContent = "🇺🇸 English";
        } else {
            tag.className = "ml-3 px-2 py-0.5 text-[11px] font-mono font-semibold rounded-md bg-ink-100 text-ink-600 border border-ink-200/70 select-none whitespace-nowrap transition-colors";
            tag.textContent = "🌐 MULTI";
        }
    }

    /* ━━━ Dynamic Rotating Multilingual Placeholder ━━━ */
    const ROTATING_PLACEHOLDERS = [
        { lang: 'ko', text: '인물, 사건, 장소를 한국어로 자유롭게 질문하세요 (예: 유관순의 아우내 장터 만세운동)' },
        { lang: 'en', text: 'Ask freely in English (e.g. Who led the protests in Pyongyang?)' },
        { lang: 'ja', text: '日本語で質問できます (例: 李東輝の武装闘争路線と新韓青年党)' },
        { lang: 'zh', text: '支持中文自然语言提问 (例: 1919年三一运动在平壤的爆发过程)' }
    ];
    let placeholderIdx = 0;
    let placeholderCharIdx = 0;
    let isDeletingPlaceholder = false;
    let placeholderTimer = null;

    function stepPlaceholder() {
        if (!$input) return;
        if (document.activeElement === $input || $input.value.trim().length > 0) {
            placeholderTimer = setTimeout(stepPlaceholder, 800);
            return;
        }
        const current = ROTATING_PLACEHOLDERS[placeholderIdx];
        const full = current.text;

        if (isDeletingPlaceholder) {
            placeholderCharIdx--;
            $input.placeholder = full.substring(0, placeholderCharIdx);
            if (placeholderCharIdx <= 0) {
                isDeletingPlaceholder = false;
                placeholderIdx = (placeholderIdx + 1) % ROTATING_PLACEHOLDERS.length;
                placeholderTimer = setTimeout(stepPlaceholder, 300);
                return;
            }
            placeholderTimer = setTimeout(stepPlaceholder, 20);
        } else {
            placeholderCharIdx++;
            $input.placeholder = full.substring(0, placeholderCharIdx);
            if (placeholderCharIdx >= full.length) {
                isDeletingPlaceholder = true;
                placeholderTimer = setTimeout(stepPlaceholder, 2500);
                return;
            }
            placeholderTimer = setTimeout(stepPlaceholder, 40);
        }
    }

    /* ━━━ Bootstrap: CSRF Token ━━━ */
    let csrfFetchingPromise = null;

    async function ensureCsrf(forceRefresh = false) {
        if (!csrfToken && !forceRefresh) {
            try {
                const cached = sessionStorage.getItem('docent_csrf');
                if (cached) csrfToken = cached;
            } catch (_) {}
        }
        if (csrfToken && !forceRefresh) return csrfToken;
        if (csrfFetchingPromise && !forceRefresh) return csrfFetchingPromise;

        csrfFetchingPromise = (async () => {
            try {
                const res = await fetch(`${API_BASE}?csrf=1`, { credentials: 'same-origin' });
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.csrf_token) {
                        csrfToken = data.csrf_token;
                        try { sessionStorage.setItem('docent_csrf', csrfToken); } catch (_) {}
                        return csrfToken;
                    }
                }
            } catch (_) {}

            try {
                const res = await fetch(API_BASE, { credentials: 'same-origin' });
                const html = await res.text();
                const m = html.match(/(?:csrfToken|docent_csrf|__PRELOADED_CSRF__)\s*[:=]\s*['"]([^'"]+)['"]/i)
                       || html.match(/['"]([a-f0-9_\-\.]{32,})['"]/);
                if (m) {
                    csrfToken = m[1];
                    try { sessionStorage.setItem('docent_csrf', csrfToken); } catch (_) {}
                    return csrfToken;
                }
            } catch (e) {
                console.warn('CSRF extraction fallback failed:', e);
            }
            return csrfToken;
        })().finally(() => {
            csrfFetchingPromise = null;
        });

        return csrfFetchingPromise;
    }

    /* ━━━ Helpers ━━━ */
    function escapeHtml(s) {
        if (s == null) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
    }

    const RECORD_LABELS = {raw_event_info:'사건정보', raw_bibliography:'서지정보', raw_source_info:'출처정보', raw_detail_place:'세부장소'};

    // Show a cited record key ("raw_source_info-14880") with the identifier of the record in the
    // March First Movement Database, which a reader can look up; the internal key stays in the tooltip.
    function labelRecordCitations(html) {
        return String(html).replace(/\b(raw_[a-z_]+?)-(\d+)\b/g, (whole, table, rowid) => {
            const sid = recordSources[`${table}-${rowid}`];
            if (!sid) return whole;
            const label = RECORD_LABELS[table] || table;
            return `<span class="record-cite" title="${escapeHtml(whole)}">${escapeHtml(label)} ${escapeHtml(sid)}</span>`;
        });
    }

    function renderMarkdown(md) {
        const safeMarkdown = String(md == null ? '' : md)
            .replace(/<\s*(script|iframe|object|embed)\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi, '')
            .replace(/<\s*\/?\s*(script|iframe|object|embed)\b[^>]*>/gi, '')
            .replace(/\bon(?:error|load)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '');
        if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
            try { return labelRecordCitations(marked.parse(safeMarkdown)); } catch (_) {}
        }
        return labelRecordCitations(escapeHtml(safeMarkdown).replace(/\n/g, '<br>'));
    }

    function setQuery(t) {
        const term = (typeof t === 'string' ? t : '').trim();
        if (!term) return;
        if ($input) $input.value = term;
        updateInputLanguageBadge();
        submitQuery(term);
    }

    function logSystem(msg) {
        const ts = new Date().toLocaleTimeString('ko-KR',{hour12:false});
        if ($systemLogContent) {
            $systemLogContent.innerHTML += `<div><span class="text-ink-300">[${ts}]</span> ${escapeHtml(msg)}</div>`;
            $systemLogContent.scrollTop = $systemLogContent.scrollHeight;
        }
    }

    function addStep(icon, text, status='active') {
        const id = 'rs-' + Date.now() + '-' + Math.floor(Math.random()*1000);
        const c = { active:'text-taegeuk-blue', done:'text-green-600', error:'text-red-500' }[status] || 'text-ink-500';
        if ($reasoningSteps) {
            $reasoningSteps.innerHTML += `<div id="${id}" class="reasoning-step flex items-center gap-2 ${c} animate-fade-in">
                <span>${icon}</span><span>${escapeHtml(text)}</span>
                ${status==='active'?'<span class="w-1 h-1 rounded-full bg-current animate-pulse-dot ml-1"></span>':''}
            </div>`;
        }
        return id;
    }

    function doneStep(id, icon, text) {
        const el = document.getElementById(id);
        if (el) {
            el.className = 'reasoning-step flex items-center gap-2 text-green-600 animate-fade-in';
            el.innerHTML = `<span>${icon}</span><span>${escapeHtml(text)}</span>`;
        }
    }

    /* ━━━ API call (CSRF 자동 첨부 및 만료시 1회 자동 재시도) ━━━ */
    async function api(action, data={}, isRetry = false) {
        await ensureCsrf();
        const fd = new FormData();
        for (let k in data) fd.append(k, data[k]);
        if (csrfToken) fd.append('csrf_token', csrfToken);
        logSystem(`→ ${action}`);
        const res = await fetch(`${API_BASE}?ajax=${action}`, {
            method:'POST', body:fd, credentials:'same-origin',
            headers: csrfToken ? {'X-CSRF-Token': csrfToken} : {}
        });
        const raw = await res.text();
        if (!res.ok) {
            let errorMsg = `HTTP ${res.status}`;
            let isCsrfError = false;
            try {
                const e = JSON.parse(raw);
                errorMsg = e.error || errorMsg;
                if (res.status === 403 && (errorMsg.includes('보안 토큰') || errorMsg.includes('CSRF'))) {
                    isCsrfError = true;
                }
            } catch (_) {}

            if (isCsrfError && !isRetry) {
                logSystem(tr(`🔄 보안 토큰 재발급 후 재시도 (${action})`, `🔄 Security token renewed; retrying (${action})`));
                try { sessionStorage.removeItem('docent_csrf'); } catch (_) {}
                csrfToken = '';
                await ensureCsrf(true);
                return api(action, data, true);
            }

            throw new Error(`[${action}] ${errorMsg}`);
        }
        try { const j=JSON.parse(raw); logSystem(`✓ ${action}`); return j; }
        catch(e) { throw new Error(`[${action}] JSON 파싱 실패`); }
    }

    /* ━━━ Source Badges ━━━ */
    function renderBadges(nodes) {
        const iconMap = {'Person':'🧑','인물':'🧑','Event':'📜','사건':'📜','Place':'📍','장소':'📍','Organization':'🏛️','기관':'🏛️','Document':'📄','문헌':'📄','사료':'📄'};
        const colorMap = {'Person':'bg-blue-50 text-blue-700 border-blue-200','인물':'bg-blue-50 text-blue-700 border-blue-200','Event':'bg-red-50 text-red-700 border-red-200','사건':'bg-red-50 text-red-700 border-red-200','Place':'bg-green-50 text-green-700 border-green-200','장소':'bg-green-50 text-green-700 border-green-200','Organization':'bg-purple-50 text-purple-700 border-purple-200','기관':'bg-purple-50 text-purple-700 border-purple-200','Document':'bg-amber-50 text-amber-700 border-amber-200','문헌':'bg-amber-50 text-amber-700 border-amber-200','사료':'bg-amber-50 text-amber-700 border-amber-200'};
        let html=''; const seen=new Set();
        nodes.forEach(n=>{
            const lbl=(n.label||'').replace(/[\n📜👤🔥📍🏢]/g,'').trim();
            if(seen.has(lbl)||!lbl) return; seen.add(lbl);
            const tp=n.type||(n.labels&&n.labels[0])||'개체';
            html+=`<button type="button" onclick="focusNode('${n.id}')" class="source-badge px-3 py-1.5 rounded-full border text-xs font-medium ${colorMap[tp]||'bg-ink-50 text-ink-600 border-ink-200'} cursor-pointer">${iconMap[tp]||'🔹'} ${escapeHtml(lbl)}</button>`;
        });
        if ($sourcesBadges) $sourcesBadges.innerHTML=html;
        if ($sourcesCount) $sourcesCount.textContent=`${seen.size}건`;
    }

    /* ━━━ Update Docent Explanation Language Badge ━━━ */
    function updateExplainLangBadge(lang) {
        const badge = $('current-explain-lang-badge');
        if (!badge) return;
        const labels = {
            ko: '🇰🇷 한국어 해설',
            en: '🇺🇸 English Commentary',
            ja: '🇯🇵 日本語解説',
            zh: '🇨🇳 中文解说'
        };
        badge.textContent = labels[lang] || `🌐 ${lang.toUpperCase()}`;
    }

    /* ━━━ Main: Submit Query ━━━ */
    async function submitQuery(text) {
        if (isSubmitting) return;
        const rawTerm = (typeof text === 'string') ? text : ($input ? $input.value : '');
        const term = (rawTerm || '').trim();
        stepKo = selectedUiLang !== 'auto' ? selectedUiLang === 'ko' : ['ko', 'auto'].includes(detectInputLanguage(term));
        if (term.length < 2) { 
            if ($input) $input.focus(); 
            return; 
        }
        isSubmitting = true;
        if ($input) $input.value = term;
        updateInputLanguageBadge();

        if ($heroSection) {
            $heroSection.classList.remove('pt-14','sm:pt-20');
            $heroSection.classList.add('pt-4','sm:pt-6');
        }
        if ($reasoningPanel) $reasoningPanel.classList.remove('hidden');
        if ($sourcesPanel) $sourcesPanel.classList.add('hidden');
        if ($graphSection) $graphSection.classList.add('hidden');
        if ($explanationSection) $explanationSection.classList.remove('hidden');
        if ($explainBtn) $explainBtn.classList.add('hidden');
        if ($nodeInfoPanel) $nodeInfoPanel.classList.add('hidden');
        if ($systemLogSection) $systemLogSection.classList.remove('hidden');
        if ($reasoningSteps) $reasoningSteps.innerHTML=''; 
        if ($sourcesBadges) $sourcesBadges.innerHTML='';
        if ($explanationContent) $explanationContent.innerHTML=`<p class="text-ink-400 italic">${tr('지식그래프 및 1차 사료를 탐색하고 있습니다...', 'Searching the knowledge graph and primary sources...')}</p>`;
        lastEvidences=[]; lastNarrative=null; narrativePromise=null; pgPrefetchTexts=[]; recordSources={}; currentNodes=[]; currentEdges=[]; analysisResult=null;
        logSystem(`${tr('검색 시작', 'Search started')}: "${term}"`);

        try {
            /* 0. Intent Router — 클라이언트 사전 분류 */
            const intentResult = classifyIntent(term);
            const strategyMeta = STRATEGY_LABELS[intentResult.intent] || STRATEGY_LABELS.FULLTEXT;
            addStep(strategyMeta.icon, `${tr('[질의 유형 · 규칙 기반 사전 분류]', '[Query type · rule-based pre-check]')} ${tr(strategyMeta.label, strategyMeta.en)} — ${intentResult.reason}`, 'done');
            logSystem(`[Router] intent=${intentResult.intent}, keywords=[${intentResult.keywords.join(',')}], reason=${intentResult.reason}`);

            const serverIntent = SERVER_INTENT[intentResult.intent] || 'ENTITY_SEARCH';
            const kws = (intentResult.keywords && intentResult.keywords.length > 0) ? intentResult.keywords : [term];
            const detectedLang = detectInputLanguage(term) || 'ko';
            const langLabels = { ko: '🇰🇷 한국어 (Korean)', en: '🇺🇸 English', ja: '🇯🇵 日本語 (Japanese)', zh: '🇨🇳 中文 (Chinese)' };
            const detectedName = langLabels[detectedLang] || `🌐 ${detectedLang.toUpperCase()}`;
            addStep('🌐', tr(`[언어 감지] 질의 언어: ${detectedName} → 한국어 지식그래프 직접 탐색`,
                `[Language] Query language: ${detectedName} → translated to Korean to search the Korean knowledge graph`), 'done');
            updateExplainLangBadge(detectedLang);

            /* 서사 경로: graph와 동시에 별도 요청으로 계산 (해설 단계에서 재사용) */
            narrativePromise = api('narrative', {term})
                .then(r => { lastNarrative = (r && Array.isArray(r.rows) && r.rows.length) ? r : null; return lastNarrative; })
                .catch(err => { logSystem(`⚠️ ${tr('narrative 생략', 'narrative skipped')}: ${err.message}`); return null; });

            /* 1. Graph: 외국어는 analyze의 한국어 변환을 먼저 반영 */
            const s2 = addStep('🔍', tr(`지식그래프에서 "${kws.join(', ')}" 탐색 중...`, `Searching the knowledge graph for "${kws.join(', ')}"...`));
            if ($reasoningTitle) $reasoningTitle.textContent=tr('지식그래프 사료 탐색 중...', 'Searching the knowledge graph...');
            logSystem(tr(`의도: ${intentResult.intent}, KW: ${kws.join(', ')}, 감지언어: ${detectedLang}`, `Intent: ${intentResult.intent}, KW: ${kws.join(', ')}, language: ${detectedLang}`));

            let gd;
            let analysis, analyzed;
            if (detectedLang === 'ko') {
                const analyzePromise = api('analyze',{term, search_strategy: serverIntent}).catch(err => {
                    logSystem(`⚠️ ${tr('analyze 실패 (그래프 탐색은 계속)', 'analyze failed (graph search continues)')}: ${err.message}`);
                    return null;
                });
                gd = await api('graph',{
                    term,
                    keywords: JSON.stringify(kws),
                    intent_ko: term,
                    search_strategy: serverIntent
                });
                analyzed = await analyzePromise;
                analysis = analyzed || {
                    intent_type: serverIntent,
                    search_keywords: kws,
                    analyzed_intent_ko: term,
                    response_language: detectedLang,
                    intent: serverIntent,
                    keywords: kws,
                    focus: '종합',
                    explanation: term
                };
            } else {
                analyzed = await api('analyze',{term, search_strategy: serverIntent}).catch(err => {
                    logSystem(`⚠️ ${tr('analyze 실패', 'analyze failed')}: ${err.message}`);
                    return null;
                });
                analysis = analyzed || {
                    intent_type: serverIntent,
                    search_keywords: kws,
                    analyzed_intent_ko: term,
                    response_language: detectedLang,
                    intent: serverIntent,
                    keywords: kws,
                    focus: '종합',
                    explanation: term
                };
                const analyzedKeywords = Array.isArray(analysis.search_keywords) && analysis.search_keywords.length > 0
                    ? analysis.search_keywords
                    : (Array.isArray(analysis.keywords) && analysis.keywords.length > 0 ? analysis.keywords : kws);
                const analyzedIntentKo = analysis.analyzed_intent_ko || analysis.intent_ko || term;
                gd = await api('graph',{
                    term,
                    keywords: JSON.stringify(analyzedKeywords),
                    intent_ko: analyzedIntentKo,
                    search_strategy: analysis.intent_type || analysis.intent || serverIntent
                });
            }
            lastEvidences=gd.evidences||[]; currentNodes=gd.nodes||[]; currentEdges=gd.edges||[];

            analysisResult = analysis;
            analysisResult._clientIntent = intentResult;
            if (analyzed && analyzed.intent_type) {
                addStep('🧭', tr(`[질의 분석] 서버 의도 분류: ${analyzed.intent_type}`, `[Query analysis] Server intent: ${analyzed.intent_type}`), 'done');
            }

            if (gd.generated_cypher) {
                const cypherBadge = gd.cypher_executed 
                    ? tr(`[Text-to-Cypher] 동적 쿼리 생성 및 실행 성공 (${gd.cypher_record_count || 0}건 반환)`, `[Text-to-Cypher] Query generated and run (${gd.cypher_record_count || 0} rows)`)
                    : tr(`[Text-to-Cypher] 동적 쿼리 생성 완료`, `[Text-to-Cypher] Query generated`);
                addStep('🕸️', `${cypherBadge} — ${gd.generated_cypher}`, 'done');
                logSystem(`[Text-to-Cypher] ${gd.generated_cypher} (${tr('매칭', 'matches')} ${gd.cypher_record_count || 0})`);
            }

            const graphDoneMsg = (gd.cypher_executed && gd.cypher_record_count > 0)
                ? tr(`동적 Cypher 탐색 완료 (${gd.cypher_record_count}건 매칭) — 노드 ${currentNodes.length}개, 관계 ${currentEdges.length}개, 근거 기록 ${lastEvidences.length}건`,
                    `Cypher search done (${gd.cypher_record_count} matches) — ${currentNodes.length} nodes, ${currentEdges.length} relations, ${lastEvidences.length} evidence records`)
                : tr(`그래프 탐색 완료 — 노드 ${currentNodes.length}개, 관계 ${currentEdges.length}개, 근거 기록 ${lastEvidences.length}건`,
                    `Graph search done — ${currentNodes.length} nodes, ${currentEdges.length} relations, ${lastEvidences.length} evidence records`);
            doneStep(s2,'✅', graphDoneMsg);
            logSystem(tr(`노드 ${currentNodes.length}, 엣지 ${currentEdges.length}, 근거 기록 ${lastEvidences.length}`, `Nodes ${currentNodes.length}, edges ${currentEdges.length}, evidence records ${lastEvidences.length}`));

            if (currentNodes.length>0) { 
                renderBadges(currentNodes); 
                if ($sourcesPanel) $sourcesPanel.classList.remove('hidden'); 
                if ($graphSection) {
                    $graphSection.classList.remove('hidden'); 
                    drawGraph(currentNodes,currentEdges);
                }
            }

            /* 3. PG prefetch */
            const pn = (Array.isArray(gd.prefetch_names)&&gd.prefetch_names.length>0) ? gd.prefetch_names : currentNodes.map(n=>n.raw_id);
            if (pn.length>0 || (gd.record_refs||[]).length>0) {
                const s3=addStep('📚', tr('PostgreSQL 사료 원문 연동 중...', 'Loading source texts from PostgreSQL...'));
                const rows = await api('pg_prefetch',{names:JSON.stringify(pn), refs:JSON.stringify(gd.record_refs||[])});
                rows.forEach(r=>{ if(r&&r.source_id) recordSources[`${r.table}-${r.rowid}`]=r.source_id; });
                pgPrefetchTexts=rows.map(r=>{let s=[];for(let k in r.snippets){if(r.snippets[k])s.push(`${k}: ${r.snippets[k]}`);}return`[${r.schema}.${r.table}] node=${r.node_id} rowid=${r.rowid} :: ${s.join('; ')}`;});
                // The server looks up at most 25 record IDs, then fills the rest by name search (handle_action_pg_prefetch).
                const refCount = (gd.record_refs || []).length;
                const byName = rows.filter(r => r && r.via === 'name').length, byRef = rows.length - byName;
                doneStep(s3,'✅',tr(`PostgreSQL 원문 ${rows.length}행 연동 완료 — 기록 식별자 ${refCount}건 중 ${byRef}행, 이름 검색 ${byName}행${refCount > 25 ? ' (식별자 조회는 최대 25건)' : ''}`,
                    `Loaded ${rows.length} source rows from PostgreSQL — ${byRef} of ${refCount} referenced records, ${byName} by name search${refCount > 25 ? ' (ID lookup capped at 25)' : ''}`));
                logSystem(`${tr('PG 원문 행', 'PG source rows')}: ${pgPrefetchTexts.length} (ID ${byRef}/${refCount}, ${tr('이름', 'name')} ${byName})`);
            }

            /* 4. Automatically trigger docent explanation */
            if ($reasoningTitle) $reasoningTitle.textContent=tr('🔍 사료 탐색 완료 → AI 도슨트 해설 생성 중...', '🔍 Sources found → writing docent commentary...');
            if ($explainBtn) $explainBtn.classList.remove('hidden');
            await generateExplanation(detectedLang);

        } catch(e) {
            console.error(e);
            addStep('❌',e.message,'error');
            if ($reasoningTitle) $reasoningTitle.textContent=tr('오류가 발생했습니다', 'An error occurred');
            if ($explanationContent) {
                $explanationContent.innerHTML=`<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700"><p class="font-semibold mb-1">${tr('데이터 탐색 실패', 'Search failed')}</p><p class="font-mono text-xs break-all">${escapeHtml(e.message)}</p></div>`;
            }
            logSystem(`${tr('오류', 'Error')}: ${e.message}`);
        } finally {
            isSubmitting = false;
        }
    }

    /* ━━━ Re-Explain in Different Language ━━━ */
    async function reExplainWithLang(targetLang) {
        if (!lastEvidences || lastEvidences.length === 0) {
            logSystem(tr('사료가 아직 수집되지 않았습니다.', 'No sources collected yet.'));
            return;
        }
        logSystem(`${tr('언어 전환 요청', 'Language change requested')}: ${targetLang.toUpperCase()}`);
        updateExplainLangBadge(targetLang);
        await generateExplanation(targetLang);
    }

    /* ━━━ Generate Explanation (SSE Stream) ━━━ */
    async function generateExplanation(overrideLang) {
        const rawTerm = $input ? $input.value : '';
        const term = rawTerm.trim(); 
        if(!term) return;
        const lang = overrideLang || (selectedUiLang !== 'auto' ? selectedUiLang : (analysisResult?.response_language || detectInputLanguage(term) || 'ko'));
        const focus = analysisResult?.focus||'';
        updateExplainLangBadge(lang);

        if ($explainBtn) $explainBtn.classList.add('hidden');
        if ($explanationContent) {
            $explanationContent.innerHTML=`<div class="flex items-center gap-2 text-ink-400 text-sm py-4">
                <span class="w-1.5 h-1.5 rounded-full bg-taegeuk-red animate-pulse-dot"></span>
                <span class="w-1.5 h-1.5 rounded-full bg-taegeuk-red animate-pulse-dot" style="animation-delay:.15s"></span>
                <span class="w-1.5 h-1.5 rounded-full bg-taegeuk-red animate-pulse-dot" style="animation-delay:.3s"></span>
                <span>${tr('사료 비판 검증 및 학술 해설 작성 중...', 'Checking sources and writing commentary...')} [${lang.toUpperCase()}]</span>
            </div>`;
        }
        const sE=addStep('✍️',`${tr('AI 도슨트가 학술 해설을 작성 중입니다...', 'The AI docent is writing commentary...')} [${lang.toUpperCase()}]`);
        logSystem(`${tr('해설 생성 시작', 'Commentary started')}: lang=${lang}`);

        try {
            await ensureCsrf();
            if (narrativePromise) {
                // 서사 경로가 아직 계산 중이면 최대 20초까지 기다린 뒤 진행 (없으면 서버가 직접 계산)
                await Promise.race([narrativePromise, new Promise(r => setTimeout(r, 20000))]);
            }
            const fd=new FormData();
            fd.append('term',term); fd.append('focus',focus); fd.append('lang',lang);
            fd.append('evidences',JSON.stringify(lastEvidences));
            fd.append('pg_texts',JSON.stringify(pgPrefetchTexts));
            if(lastNarrative) fd.append('narrative',JSON.stringify(lastNarrative));
            fd.append('stream','1');
            if(csrfToken) { fd.append('csrf_token',csrfToken); }

            let res=await fetch(`${API_BASE}?ajax=explain&stream=1`,{
                method:'POST',body:fd,credentials:'same-origin',
                headers: csrfToken?{'X-CSRF-Token':csrfToken}:{}
            });
            if(res.status === 403) {
                try { sessionStorage.removeItem('docent_csrf'); } catch (_) {}
                csrfToken = '';
                await ensureCsrf(true);
                if(csrfToken) {
                    fd.set('csrf_token', csrfToken);
                    res=await fetch(`${API_BASE}?ajax=explain&stream=1`,{
                        method:'POST',body:fd,credentials:'same-origin',
                        headers: {'X-CSRF-Token':csrfToken}
                    });
                }
            }
            if(!res.ok) throw new Error(`HTTP ${res.status}`);

            const reader=res.body.getReader(), dec=new TextDecoder('utf-8');
            let full='', buf='';
            while(true){
                const{value,done}=await reader.read(); if(done) break;
                buf+=dec.decode(value,{stream:true});
                const lines=buf.split('\n'); buf=lines.pop();
                for(const l of lines){
                    const t=l.trim();
                    if(t==='data: [DONE]') break;
                    if(t.startsWith('data: ')){
                        try{
                            const p=JSON.parse(t.substring(6));
                            if(p.chunk){
                                full+=p.chunk;
                                if ($explanationContent) {
                                    $explanationContent.innerHTML=renderMarkdown(full)+'<span class="typing-cursor">▌</span>';
                                }
                            }
                        }catch(e){}
                    }
                }
            }
            if ($explanationContent) $explanationContent.innerHTML=renderMarkdown(full);
            doneStep(sE,'✅',`${tr('AI 도슨트 해설 작성 완료', 'Docent commentary complete')} [${lang.toUpperCase()}]`);
            if ($reasoningTitle) $reasoningTitle.textContent=tr('✨ 도슨트 해설 완료', '✨ Commentary complete');
            if ($explainBtn) $explainBtn.classList.remove('hidden');
            logSystem(`${tr('해설 완료', 'Commentary complete')} [${lang.toUpperCase()}]`);
        } catch(e) {
            console.error(e);
            if ($explanationContent) {
                $explanationContent.innerHTML=`<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700"><p class="font-semibold mb-1">${tr('해설 생성 실패', 'Commentary failed')}</p><p class="font-mono text-xs break-all">${escapeHtml(e.message)}</p></div>`;
            }
            doneStep(sE,'❌',`${tr('실패', 'Failed')}: ${e.message}`);
            if ($explainBtn) $explainBtn.classList.remove('hidden');
            logSystem(`${tr('해설 오류', 'Commentary error')}: ${e.message}`);
        }
    }

    /* ━━━ Knowledge Graph (vis.js) ━━━ */
    function drawGraph(nodes,edges){
        const c=$('graph-container');
        if (!c) return;
        if (typeof vis === 'undefined' || !vis.DataSet || !vis.Network) {
            logSystem(tr('⚠️ vis.js 로딩 대기 중...', '⚠️ Waiting for vis.js to load...'));
            return;
        }
        try {
            const d={nodes:new vis.DataSet(nodes),edges:new vis.DataSet(edges)};
            const o={
                edges:{arrows:'to',color:{color:'#9ca3af',hover:'#6b7280',highlight:'#C8102E'},font:{size:11,align:'middle',color:'#6b7280'},smooth:{type:'continuous'}},
                physics:{enabled:true,repulsion:{nodeDistance:240,centralGravity:0.15},stabilization:{iterations:120}},
                interaction:{hover:true,tooltipDelay:200}
            };
            if(network) network.destroy();
            network=new vis.Network(c,d,o);
            network.on('click',p=>{
                if(p.nodes.length>0){const n=currentNodes.find(x=>x.id===p.nodes[0]);if(n) showNodeInfo(n);}
                else if ($nodeInfoPanel) $nodeInfoPanel.classList.add('hidden');
            });
        } catch(err) {
            console.warn('vis.js drawGraph error:', err);
        }
    }

    function centerGraph(){
        if(network) network.fit({animation:{duration:600,easingFunction:'easeInOutQuad'}});
    }

    function focusNode(id){
        if(network){
            network.focus(id,{scale:1.2,animation:{duration:500,easingFunction:'easeInOutQuad'}});
            network.selectNodes([id]);
        }
        const n=currentNodes.find(x=>x.id===id);
        if(n) showNodeInfo(n);
    }

    /* ━━━ Node Info ━━━ */
    function showNodeInfo(node){
        const lbl=(node.label||'').replace(/[\n📜👤🔥📍🏢]/g,'').trim();
        let h=`<div class="flex items-center justify-between mb-3"><h3 class="font-bold text-ink-900 text-base">${escapeHtml(lbl)}</h3><span class="px-2.5 py-1 rounded-full text-xs font-medium bg-taegeuk-blue-light text-taegeuk-blue">${escapeHtml(node.type||'개체')}</span></div>`;
        h+=`<div class="text-xs text-ink-400 mb-3 font-mono break-all">ID: ${escapeHtml(node.raw_id||node.id)}</div>`;
        if(Array.isArray(node.aliases)&&node.aliases.length) h+=`<div class="mb-3"><span class="text-xs font-medium text-ink-500">이칭:</span> <span class="text-sm text-ink-700">${escapeHtml(node.aliases.join(', '))}</span></div>`;

        if(node.props&&Object.keys(node.props).length){
            h+=`<div class="border-t border-ink-100 pt-3 mt-3"><h4 class="text-xs font-semibold text-ink-500 mb-2">메타데이터</h4><div class="max-h-[180px] overflow-y-auto"><table class="w-full text-xs">`;
            for(let k in node.props){if(['embedding','labels','id','uid'].includes(k))continue;const v=String(node.props[k]||'').trim();if(!v)continue;h+=`<tr class="border-b border-ink-50"><td class="py-1.5 pr-3 text-ink-400 font-medium whitespace-nowrap">${escapeHtml(k)}</td><td class="py-1.5 text-ink-700 break-all" style="white-space:pre-wrap">${escapeHtml(v)}</td></tr>`;}
            h+=`</table></div></div>`;
        }

        const ce=currentEdges.filter(e=>e.from===node.id||e.to===node.id);
        if(ce.length){
            h+=`<div class="border-t border-ink-100 pt-3 mt-3"><h4 class="text-xs font-semibold text-ink-500 mb-2">연결 (${ce.length}건)</h4><div class="flex flex-wrap gap-1.5 max-h-[120px] overflow-y-auto">`;
            ce.forEach(e=>{const o=e.from===node.id?e.to:e.from;const on=currentNodes.find(x=>x.id===o);if(on){const ol=(on.label||'').replace(/[\n📜👤🔥📍🏢]/g,'').trim();h+=`<button type="button" onclick="focusNode('${o}')" class="px-2.5 py-1 rounded-lg border border-ink-200 text-xs text-ink-600 hover:bg-ink-50 cursor-pointer"><span class="text-ink-400">${escapeHtml(e.label||'→')}</span> ${escapeHtml(ol)}</button>`;}});
            h+=`</div></div>`;
        }

        h+=`<div id="node-pg-source-box" class="border-t border-ink-100 pt-3 mt-3"><div class="flex items-center gap-2 text-xs text-ink-400"><span class="w-3 h-3 border-2 border-ink-300 border-t-transparent rounded-full animate-spin"></span>원천 사료 조회 중...</div></div>`;
        if ($nodeInfoContent) $nodeInfoContent.innerHTML=h;
        if ($nodeInfoPanel) $nodeInfoPanel.classList.remove('hidden');
        loadNodePg(node);
    }

    /* ━━━ TEI 렌더링 ━━━
     * 레코드 TEI를 안전한 HTML로 변환합니다. 허용한 요소만 HTML로 바꾸고
     * 텍스트는 모두 이스케이프합니다(사료 텍스트가 HTML로 실행되지 않음).
     *   ruby/rb/rt  → HTML ruby (한자 위에 한글 독음)
     *   persName    → 인물 표시, 독립유공자 인명사전 URL이 있으면 링크
     *   placeName   → 장소 표시, orgName → 기관 표시
     *   p[data-col] → "컬럼명: 내용" 행
     */
    const TEI_TRUSTED_HOSTS = ['search.i815.or.kr'];

    function teiSafeUrl(ref) {
        try {
            const u = new URL(String(ref || ''));
            return (u.protocol === 'https:' || u.protocol === 'http:') && TEI_TRUSTED_HOSTS.includes(u.hostname) ? u.href : '';
        } catch (e) { return ''; }
    }

    function teiInline(node) {
        if (node.nodeType === 3) return escapeHtml(node.nodeValue);
        if (node.nodeType !== 1) return '';
        const name = node.localName;
        const inner = () => Array.from(node.childNodes).map(teiInline).join('');
        switch (name) {
            case 'ruby': return `<ruby class="tei-ruby">${inner()}</ruby>`;
            case 'rb': return inner();
            case 'rt': return `<rt>${escapeHtml(node.textContent)}</rt>`;
            case 'persName': {
                const ref = node.getAttribute('ref') || '';
                const url = teiSafeUrl(ref);
                if (url) return `<a class="tei-pers tei-linked" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="독립유공자 인명사전">${inner()}</a>`;
                return `<span class="tei-pers" title="인물${ref ? ' ' + escapeHtml(ref) : ''}">${inner()}</span>`;
            }
            case 'placeName': return `<span class="tei-place" title="장소">${inner()}</span>`;
            case 'orgName': return `<span class="tei-org" title="기관">${inner()}</span>`;
            case 'lb': return '<br>';
            default: return inner();
        }
    }

    function renderTeiHtml(tei) {
        if (!tei) return '';
        let doc = new DOMParser().parseFromString(String(tei), 'application/xml');
        if (doc.getElementsByTagName('parsererror').length) return '';
        const scope = doc.getElementsByTagName('body')[0] || doc.documentElement;
        const rows = [];
        for (const p of Array.from(scope.getElementsByTagName('p'))) {
            const html = Array.from(p.childNodes).map(teiInline).join('').trim();
            if (!html) continue;
            const col = p.getAttribute('data-col');
            rows.push(`<tr class="border-b border-ink-50">${col ? `<td class="py-1.5 pr-3 text-ink-400 font-medium whitespace-nowrap align-top">${escapeHtml(col)}</td>` : '<td></td>'}<td class="py-1.5 text-ink-700 break-all tei-text" style="white-space:pre-wrap">${html}</td></tr>`);
        }
        if (!rows.length) return '';
        return `<div class="tei-legend text-[11px] text-ink-400 mb-1"><span class="tei-pers">인물</span> <span class="tei-place">장소</span> <span class="tei-org">기관</span> · 한자 위 작은 글씨는 한글 독음</div><table class="w-full text-xs">${rows.join('')}</table>`;
    }

    async function loadNodePg(node){
        const box=$('node-pg-source-box'); if(!box) return;
        const p=node.props||{}, tbl=p['원천테이블']||p['source_table']||'', rid=p['원천rowid']||p['rowid']||'';
        const lbl=(node.label||'').replace(/[\n📜👤🔥📍🏢]/g,'').trim();
        try{
            const d=await api('node_detail',{table:tbl,rowid:rid,raw_id:node.raw_id||'',label:lbl,aliases:JSON.stringify(node.aliases||[])});
            if(d&&d.found&&d.columns&&Object.keys(d.columns).length){
                const srcName=d.source_id?`${escapeHtml(d.source_label||d.table)} ${escapeHtml(d.source_id)}`:`${escapeHtml(d.table)} #${d.rowid}`;
                let rh=`<h4 class="text-xs font-semibold text-green-600 mb-2" title="${escapeHtml(d.table)} #${d.rowid}">📋 원천 사료 [${srcName}]</h4><div class="max-h-[200px] overflow-y-auto"><table class="w-full text-xs">`;
                for(let c in d.columns){const v=d.columns[c];if(v==null||v==='')continue;rh+=`<tr class="border-b border-ink-50"><td class="py-1.5 pr-3 text-ink-400 font-medium whitespace-nowrap">${escapeHtml(c)}</td><td class="py-1.5 text-ink-700 break-all" style="white-space:pre-wrap">${escapeHtml(v)}</td></tr>`;}
                rh+=`</table></div>`;
                if(d.tei){const teiHtml=renderTeiHtml(d.tei); if(teiHtml) rh+=`<details class="mt-2" open><summary class="text-xs text-taegeuk-blue cursor-pointer font-medium">📜 TEI 주석 보기</summary><div class="mt-1 bg-ink-50 p-3 rounded-lg overflow-auto max-h-[260px]">${teiHtml}</div></details>`;}
                if(d.tei) rh+=`<details class="mt-2"><summary class="text-xs text-taegeuk-blue cursor-pointer font-medium">📜 TEI XML 원문</summary><pre class="mt-1 text-xs bg-ink-50 p-3 rounded-lg overflow-auto max-h-[160px] font-mono text-ink-600" style="white-space:pre-wrap">${escapeHtml(d.tei)}</pre></details>`;
                box.innerHTML=rh;
            } else { box.innerHTML=`<div class="text-xs text-ink-300">원천 DB에 직접 대응 데이터 없음.</div>`; }
        } catch(e){ box.innerHTML=`<div class="text-xs text-ink-300">조회 생략: ${escapeHtml(e.message)}</div>`; }
    }

    /* ━━━ Initialization ━━━ */
    document.addEventListener('DOMContentLoaded', () => {
        initDomRefs();
        ensureCsrf();
        placeholderTimer = setTimeout(stepPlaceholder, 1000);
    });

    /* ━━━ Global window bindings for HTML onclick execution ━━━ */
    window.setQuery = setQuery;
    window.submitQuery = submitQuery;
    window.generateExplanation = generateExplanation;
    window.reExplainWithLang = reExplainWithLang;
    window.centerGraph = centerGraph;
    window.focusNode = focusNode;
    window.setUiLanguage = setUiLanguage;
    window.classifyIntent = classifyIntent;

})();
