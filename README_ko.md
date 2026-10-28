# D-CENT

3·1운동 원자료를 PostgreSQL·TEI·CIDOC-CRM·Neo4j로 연결하고, 질문마다 날짜 순 사건 서사를
사료 인용과 함께 재구성하는 연구용 저장소입니다. English: [README.md](./README.md).

## 구성

| 경로 | 기능 | 실행 |
|---|---|---|
| [pipeline/](./pipeline/) | 데이터 구축: 원자료 → PostgreSQL/TEI → Neo4j 그래프 | `python -m pipeline` |
| [docent.php](./docent.php), [includes/](./includes/), [static/](./static/) | 웹 도슨트 (질의 분석, Cypher 생성, 서사 경로, 2단계 생성) | PHP 서버 |
| [evaluation/](./evaluation/) | 평가: 벤치마크, 시스템 비교, 전문가 평가, 리포트 | `python -m evaluation` |
| [mcp_server.py](./mcp_server.py) | MCP 클라이언트용 읽기 전용 DB 도구 | `python mcp_server.py` |
| [docent_cli.php](./docent_cli.php) | 도슨트 처리 과정을 터미널에서 확인 (디버깅) | `php docent_cli.php "질문" ko` |
| [Latex/](./Latex/) | 논문 원고 | |

```text
pipeline/
  __main__.py   명령: build, check, inspect
  config.py     설정(환경변수 → .streamlit/secrets.toml), 경로, DB 연결
  text.py       한자 독음(두음법칙)·TEI ruby 처리
  ingest.py     1  원자료 → raw_* 테이블, 행 단위 TEI
  entities.py   2  인명 태깅(한자 독음 ruby, persName), 검토용 개체 목록
  authority.py  3  인명사전(i815) 연결
  cidoc.py      4  CIDOC-CRM 매핑
  indexes.py    5  PostgreSQL 검색 인덱스
  graph.py      6  Neo4j 그래프
  thesaurus.py  7  한국역사용어시소러스
  names.py      8  이름 독음·로마자 표기, 언급(Activity) 노드 라벨
  inspect.py    읽기 전용 진단
  check.py      오프라인 자체 점검
evaluation/
  __main__.py   명령: dataset, run, report, judge, expert, rate
  common.py     경로, 통계(부트스트랩, 순열 검정, Holm, κ), 엑셀 워크북
  benchmark.py  벤치마크 생성·검증
  systems.py    비교 시스템과 공통 조건
  metrics.py    자동 지표, 인용 검증, 쌍대 비교
  run.py        실행·채점, context precision 판정
  human.py      전문가 그래프 검증, 답변 블라인드 평가
  report.py     리포트(report.md, LaTeX 표, 콘솔 요약)
  dataset/      questions.json(후보), benchmark.json(검증 통과), third_party/(외부 출제)
  annotation/   전문가용 워크북 (expert/, rating/, rating_key/)
  results/      실행별 결과 폴더, graph/(그래프 규모·전문가 검증)
```

  ## 전체 시스템 흐름

  ```mermaid
  flowchart LR
    %% 데이터 구축 파이프라인
    subgraph DATA["1. 데이터 구축 파이프라인"]
      RAW["data/*.csv<br/>원자료"]
      INGEST["pipeline ingest<br/>raw_* 테이블 적재"]
      ENTITY["pipeline entities<br/>TEI 생성 및 인명 태깅"]
      AUTH["pipeline authority<br/>i815 인명사전 연결"]
      CIDOC["pipeline cidoc<br/>CIDOC-CRM 매핑"]
      INDEX["pipeline indexes<br/>PostgreSQL 검색 인덱스"]
      GRAPH["pipeline graph<br/>Neo4j 노드 및 관계 생성"]
      THESAURUS["pipeline thesaurus<br/>한국역사용어시소러스"]
      NAMES["pipeline names<br/>독음·로마자 표기·namesIndex"]

      RAW --> INGEST --> ENTITY --> AUTH --> CIDOC --> INDEX
      INDEX --> GRAPH --> THESAURUS --> NAMES
    end

    PG[("PostgreSQL<br/>원문·TEI·인덱스")]
    NEO[("Neo4j<br/>CIDOC 그래프")]
    NAMESDB[("namesIndex<br/>인명 검색 색인")]

    INGEST --> PG
    INDEX --> PG
    GRAPH --> NEO
    NAMES --> NAMESDB

    CHECK["pipeline check<br/>오프라인 자체 점검"] -.-> DATA
    INSPECT["pipeline inspect<br/>DB 및 그래프 진단"] -.-> PG
    INSPECT -.-> NEO

    %% 웹 도슨트
    subgraph WEB["2. 웹 도슨트 질의 흐름"]
      USER["사용자"] --> BROWSER["브라우저<br/>static/docent.js"]
      BROWSER --> ROUTER["docent.php<br/>POST·Origin·CSRF·rate limit"]
      ROUTER --> ACTION["includes/actions.php<br/>action dispatch"]

      ACTION --> ANALYZE["질의 분석<br/>언어·초점·키워드"]
      ACTION --> NARRATIVE["서사 경로 생성<br/>날짜순 사건 및 연결"]
      ANALYZE --> GQUERY["그래프 질의<br/>Text-to-Cypher 및 교차 확인"]
      NARRATIVE --> GQUERY
      GQUERY --> CONTEXT["문맥 조립<br/>서사 경로 → 그래프 근거 → 원문"]
      GQUERY --> FETCH["원문 조회<br/>record_refs 및 이름 검색"]
      FETCH --> CONTEXT
      CONTEXT --> FACT["Gemini 1단계<br/>팩트 표"]
      FACT --> EXPLAIN["Gemini 2단계<br/>근거 기반 해설"]
      EXPLAIN --> RESPONSE["그래프·출처·답변"]
      RESPONSE --> BROWSER
    end

    GQUERY --> NEO
    GQUERY --> NAMESDB
    FETCH --> PG
    NARRATIVE --> NEO
    ANALYZE --> GEMINI[("Gemini API")]
    FACT --> GEMINI
    EXPLAIN --> GEMINI

    %% 평가 파이프라인
    subgraph EVAL["3. 평가 파이프라인"]
      QUESTIONS["dataset/questions.json"] --> DATASET["evaluation dataset<br/>문항 검증 및 benchmark 생성"]
      DATASET --> BENCH["benchmark.json"]
      BENCH --> RUN["evaluation run<br/>답변 생성"]
      RUN --> SYSTEMS{"비교 시스템"}
      SYSTEMS --> PASSAGE["passage<br/>문자열 검색"]
      SYSTEMS --> HYBRID["hybrid<br/>BM25·dense·RRF"]
      SYSTEMS --> GRAPH_SYS["graph<br/>Neo4j 검색"]
      SYSTEMS --> DCENT["dcent<br/>웹 도슨트 HTTP 호출"]
      SYSTEMS --> ABLATIONS["controlled·no-paths<br/>one-stage·hybrid-two-stage"]

      PASSAGE --> ANSWERS["answers.json"]
      HYBRID --> ANSWERS
      GRAPH_SYS --> ANSWERS
      DCENT --> ANSWERS
      ABLATIONS --> ANSWERS

      ANSWERS --> METRICS["metrics<br/>자동 점수"]
      METRICS --> SCORES["scores.csv"]
      ANSWERS --> JUDGE["evaluation judge<br/>문맥 정밀도 판정"]
      JUDGE --> JUDGE_RESULT["judge.json"]
      JUDGE --> OLLAMA[("Ollama")]

      ANSWERS --> RATE_EXPORT["rate export<br/>블라인드 평가 워크북"]
      RATE_EXPORT --> HUMAN["사람 평가"]
      HUMAN --> RATING["rating.json"]

      GRAPH_SYS --> EXPERT_EXPORT["expert export<br/>전문가 주석 워크북"]
      EXPERT_EXPORT --> EXPERT["expert score"]
      EXPERT --> EXPERT_RESULT["expert.json"]

      SCORES --> REPORT["evaluation report<br/>전체 결과 통합"]
      JUDGE_RESULT --> REPORT
      RATING --> REPORT
      EXPERT_RESULT --> REPORT
      REPORT --> REPORT_OUT["report.md·LaTeX 표·통계"]
    end

    PASSAGE -.-> PG
    HYBRID -.-> PG
    GRAPH_SYS -.-> NEO
    DCENT -.-> ROUTER
    ABLATIONS -.-> ROUTER
  ```

## 설치와 설정

- Python 3.11 이상, PostgreSQL, Neo4j 5.x, PHP와 Composer
- 평가에만: Ollama(하이브리드 기준선 임베딩 `nomic-embed-text`, context precision 판정 `gemma4:26b`)

```bash
python3 -m venv .venv3.14
.venv3.14/bin/python -m pip install -r requirements.txt
composer install
```

설정은 환경변수가 우선이고, 없으면 `.streamlit/secrets.toml`에서 읽습니다. 비밀번호와 키는 커밋하지 마십시오.

```toml
PG_HOST = "localhost"
PG_PORT = "5432"
PG_DATABASE = "historical"
PG_USER = "postgres"
PG_PASSWORD = "change-me"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "change-me"
GEMINI_API_KEY = "..."
GEMINI_MODEL = "gemini-flash-lite-latest"

# 평가 (필요할 때만)
DOCENT_URL = "http://localhost:8000/docent.php"   # dcent 시스템이 호출할 서버
EVAL_DELAY = "15"                                 # 호출 간격(초)
```

웹 서버 쪽 선택 설정: `DOCENT_CONTEXT_CHARS`(생성 문맥 예산, 기본 11000), `DOCENT_EVAL_MODE=1`(평가 모드 허용),
`GEMINI_TEMPERATURE`, `GEMINI_TOP_P`.

## 데이터 구축

```bash
.venv3.14/bin/python -m pipeline build          # 전체 (PostgreSQL/TEI → Neo4j)
.venv3.14/bin/python -m pipeline build rdbms    # 1~5단계만
.venv3.14/bin/python -m pipeline build graph    # 6~8단계만 (PostgreSQL 결과가 있을 때)
.venv3.14/bin/python -m pipeline build --yes    # 개체 목록 검토 일시정지 없이
.venv3.14/bin/python -m pipeline check          # DB 없이 자체 점검
```

단계는 각각 별도 프로세스로 실행되고, 실패하면 거기서 멈춥니다. 2단계 뒤에는
`data/output/Extracted_Historical_Entities.xlsx`를 검토하도록 멈추며 Enter로 계속합니다.
파이프라인은 임베딩을 만들지 않습니다.

- 1단계는 `raw_*` 테이블을 지우고 다시 만듭니다. 운영 DB에서는 백업을 확인하십시오.
- 6~8단계는 Neo4j 그래프를 다시 만듭니다.
- i815 인명사전 색인은 `data/i815_person_index.json`에 저장됩니다. 새로 받으려면 파일을 지우십시오.
- 시소러스 CSV는 `data/`의 기본 파일 또는 `THESAURUS_CSV`입니다.

진단(읽기 전용):

```bash
.venv3.14/bin/python -m pipeline inspect names "보성사에서 결성된 비밀결사는?"   # namesIndex 검색 결과
.venv3.14/bin/python -m pipeline inspect family 유관순                       # 가족 관계 데이터
.venv3.14/bin/python -m pipeline inspect search 유관순 아우내                # 기록 검색 시간·인덱스 사용
.venv3.14/bin/python -m pipeline inspect counts                              # 그래프 규모 → evaluation/results/graph/
.venv3.14/bin/python -m pipeline inspect sparsity                            # 사건 계층 희소성 → evaluation/results/graph/
.venv3.14/bin/python -m pipeline inspect sources                             # data/ 원본 파일 행 수·식별자 대조 → evaluation/results/graph/
```

## 웹 도슨트

```bash
php -S 127.0.0.1:8000      # 저장소 루트에서
```

<http://127.0.0.1:8000/docent.php>를 엽니다. 질의 하나는 다음 요청으로 처리됩니다.

| Action | 기능 |
|---|---|
| `narrative` | 질문의 인물·장소에서 탐색 틀로 날짜 순 서사 경로 계산 (graph와 동시에) |
| `analyze` | 질의 언어·초점·키워드 분석 |
| `graph` | Text-to-Cypher(읽기 전용 검증) + 전문 색인 교차 확인, 문서 노드의 기록 식별자 |
| `pg_prefetch` | 기록 식별자로 원문 조회, 남는 자리는 이름 검색 |
| `explain` | 문맥 예산(서사 경로 → 그래프 근거 → 원문) → 팩트 표 → 해설 |
| `node_detail` | 그래프 노드의 원문 상세 |

생성된 Cypher와 단계별 소요 시간은 PHP error log에 `[Docent]`로 기록됩니다. 사건 날짜는 Event 노드의
속성이며 관계의 유효기간은 모델링되어 있지 않습니다.

## 평가

```bash
.venv3.14/bin/python -m evaluation dataset              # 질문 검증 → dataset/benchmark.json
.venv3.14/bin/python -m evaluation run --quick          # 3문항으로 점검
.venv3.14/bin/python -m evaluation run                  # 전체 실행 → results/<날짜_시각>/report.md
.venv3.14/bin/python -m evaluation run --resume         # 중단된 실행 이어서
.venv3.14/bin/python -m evaluation report               # 최근 실행의 리포트
.venv3.14/bin/python -m evaluation report RUN1 RUN2     # 여러 실행을 합친 리포트
.venv3.14/bin/python -m evaluation judge                # context precision (보조 지표, Ollama)
```

`run` 옵션은 `--quick`, `--limit N`, `--systems`, `--resume`뿐입니다. 생성 조건은 논문의 공통 조건으로
고정되어 있습니다: temperature 0, top-p 0.8, 답변 출력 1,500토큰, 질문당 근거 11,000자, 질문에서 한 번
추출한 개체로 검색(정답 개체는 어떤 시스템도 받지 않음), 실패 시 2회 재시도.

| `--systems` 키 | 시스템 |
|---|---|
| `passage` | 문단 기준선: 개체 문자열을 포함한 서지 기록 |
| `hybrid` | 하이브리드 기준선: BM25 + dense → RRF → cross-encoder 재순위 |
| `graph` | 그래프 단독 기준선: 개체 이름으로 찾은 triple |
| `dcent` | D-CENT 엔드투엔드: 배포 시스템을 HTTP로 호출 |
| `controlled` | D-CENT 통제 조건: 이름 일치 triple과 기록, 2단계 생성 |
| `no-paths` | 비교 조건 A: 엔드투엔드에서 서사 경로 제외 |
| `one-stage` | 비교 조건 B: 통제 조건 근거를 한 번의 호출로 |
| `hybrid-two-stage` | 비교 조건 C: 하이브리드 근거를 2단계 생성으로 |

`main`(앞의 넷), `ablations`(뒤의 넷), `all`(기본값) 또는 쉼표로 고릅니다.
`dcent`와 `no-paths`는 실행 중인 웹 도슨트를 호출하므로 서버를 평가 모드로 띄우고 `DOCENT_URL`을 설정합니다.

```bash
DOCENT_EVAL_MODE=1 php -S localhost:8000     # 다른 터미널에서
```

평가 모드의 `explain`은 생성에 쓴 문맥과 팩트 표를 함께 돌려주고, 공통 생성 조건을 적용하며, 기록
식별자 인용을 요구합니다. 검색과 문맥 구성은 배포 경로 그대로입니다. `explain`은 분당 5회로 제한되므로
`EVAL_DELAY`는 15초 이상으로 두십시오.

새 빌드는 `data/output/build_manifest.json`에 빌드 식별자를 남깁니다. 매니페스트가 없는
기존 DB는 고정한 스냅샷의 식별자를 `EVAL_DATA_BUILD_ID`로 지정합니다. 엔드투엔드 평가에서는
PHP 서버는 같은 매니페스트의 빌드 식별자를 자동으로 씁니다. 매니페스트가 없는 DB에서만
`DOCENT_DATA_BUILD_ID`를 같은 식별자로 지정하세요. 어느 DB든 변경하면 식별자도
갱신하고, 평가 중에는 두 DB를 고정합니다. `--resume`은 코드·벤치마크·패키지·모델·서버 설정·
빌드 식별자가 달라지면 중단합니다. 지문이 없는 옛 실행은 새로 시작해야 합니다.
답변마다 체크포인트를 원자적으로 저장하며, 평가 모드는 다른 모델로 전환하지 않고 실제 서버
모델 버전을 기록합니다. 공통 생성 조건의 수치는 유지됩니다. 지표와 근거 항목 구분이 수정되었으므로
새 평가를 실행하고 이전 결과와 합산하지 마세요.

### 실행 결과

`results/<날짜_시각>/`에 `config.json`(조건·모델·코드 버전), `answers.json`(답변과 근거),
`scores.csv`(질문×시스템 점수), `report.md`, `table_main.tex`, `table_by_category.tex`, `case_study.md`(질문 하나의 D-CENT·기준선 답변 비교), `error_analysis.xlsx`(D-CENT 답변 20개의 실패 유형 표시용, 채운 뒤 `report`를 다시 실행하면 집계됨)가 생깁니다.

리포트에는 시스템별 지표(평균과 95% 부트스트랩 CI), 계획된 비교(RQ1: D-CENT 대 기준선, RQ2: 서사 경로·
쿼리 생성·2단계 생성·근거의 기여; 쌍대 순열 검정, 지표별 Holm 보정), 범주별·출처별 결과, 인용 검증,
context precision, 전문가 평가, 그래프 규모, 직접 확인할 항목(생성 실패, 함정 동의로 표시된 문장)이
들어갑니다. 여러 실행을 합치면 질문별로 실행 평균을 낸 뒤 검정하고, 실행 간 변동을 함께 보여줍니다.

### 벤치마크

`dataset`은 `dataset/questions.json`을 검증해 통과한 문항(조사 교정 포함)을 `dataset/benchmark.json`에
씁니다. 검증 워크북(사람 검토용 30% 표본 포함)과 논문용 구성표 `benchmark_table.tex`는 `dataset/validation/`에 생깁니다.
템플릿 문항의 기준 사실은 사건 대장에 기록된 것(관련 인물, 날짜, 장소, 관련 사건 연결, 날짜로 정해지는 순서)만 씁니다.
`dataset --generate`는 사건 대장에서 템플릿 문항을 다시 만들고(저자·외부 출제 문항은 유지),
`dataset/third_party/*.json`의 외부 출제 문항을 병합합니다. 형식은
[third_party_template.json](./evaluation/dataset/third_party_template.json)을 따르며 `author`를 기입해야 합니다.

### 전문가 평가

```bash
.venv3.14/bin/python -m evaluation expert export       # annotation/expert/ 에 검증자별 워크북 (사료 150건)
.venv3.14/bin/python -m evaluation expert score        # → results/graph/expert.json
.venv3.14/bin/python -m evaluation rate export [RUN] [--n 40]   # annotation/rating/ 에 평가자별 워크북
.venv3.14/bin/python -m evaluation rate score          # → results/<RUN>/rating.json
```

답변 평가는 RQ1의 네 시스템(passage, hybrid, graph, dcent)의 답변을 섞어 시스템 이름 없이 보여줍니다.
`annotation/rating/` 폴더만 평가자에게 전달하고, `annotation/rating_key/`의 블라인드 키는 전달하지 마십시오.

## MCP 서버

[mcp_server.py](./mcp_server.py)는 `query_postgres`(SELECT/WITH), `get_postgres_schema`, `query_neo4j`,
`get_neo4j_schema`를 제공합니다. [mcp_client_config.json](./mcp_client_config.json)의 경로를 환경에 맞게
고쳐 등록하고, 비밀정보는 설정 파일이 아닌 환경변수나 secrets.toml에 두십시오.

MCP에는 구축 계정과 다른 최소 권한 계정이 필요합니다. `MCP_PG_USER` / `MCP_PG_PASSWORD`,
`MCP_NEO4J_USER` / `MCP_NEO4J_PASSWORD`를 설정하세요. PostgreSQL은 필요한 표의 SELECT만,
Neo4j는 대상 DB 읽기만 허용하고 소유권·쓰기·스키마 변경·위험한 함수/프로시저 실행 권한은
부여하지 마세요. 계정 생성과 권한 부여는 관리자가 수행해야 합니다. 계정 미설정 또는 구축 계정과
같은 사용자명이면 연결을 거절합니다. PostgreSQL 읽기 전용 트랜잭션, Neo4j 읽기 모드와 실행 계획
확인, 15초 제한, 반환 500행 제한을 적용하며 사용자 Cypher 프로시저·다중 문장·주석은 거부합니다.

## 점검

```bash
.venv3.14/bin/python -m pipeline check
php -l docent.php && php -l includes/actions.php
.venv3.14/bin/python -B -m unittest discover -s tests -v
php -n tests/gemini_safety.php
# 별도 테스트 폴더에 jsdom@26.1.0 설치 후:
NODE_PATH=/path/to/test/node_modules node tests/rendering_safety.cjs
```

## 운영상 주의

- `pipeline build`는 PostgreSQL 테이블과 Neo4j 그래프를 다시 만듭니다. 대상과 백업을 확인하십시오.
- 원자료, TEI, 기준 사실(ground truth), 검색 문맥은 서로 다른 증거 층위입니다. 모델 응답에 맞춰 기준 사실을
  고치지 말고 원문과 그래프 근거를 먼저 확인하십시오.
- API 키, DB 비밀번호, 원격 접속 정보를 로그와 커밋에 남기지 마십시오.

## 라이선스

[LICENSE](./LICENSE)를 참조하십시오.
