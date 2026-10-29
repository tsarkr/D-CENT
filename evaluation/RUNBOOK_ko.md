# 논문 완성까지 남은 일 (저자용)

명령은 저장소 루트에서 `.venv3.14/bin/python -m ...`으로 실행합니다. 역사학자에게는 이 문서가 아니라
[ANNOTATION_GUIDE_ko.md](ANNOTATION_GUIDE_ko.md)와 워크북만 전달합니다.

## 0. 그래프 재구축 (가장 먼저)

> **10월 26일 다시 필요:** 관계(참여·장소·친족)가 근거 기록 목록(`sources`)을 지니도록 그래프 코드를 바꿨습니다.
> 재구축 → `dataset --generate` → 평가 실행 순서로 진행하세요. 전문가 워크북은 TEI 태깅으로 만들므로
> 재구축 뒤 다시 내보내도 같은 표본·항목이 나와야 합니다(기록 150건, 항목 수 동일 여부만 확인).

기록 번호(rowid)를 원본 식별자 순으로 매기고, 언급 노드에 원본 식별자를 싣도록 코드를 바꿨습니다(10월 22일).
재구축하면 rowid와 그에 기반한 인용 번호(`raw_source_info-14880` 형식)가 바뀌므로, 평가 실행과 전문가 워크북
export보다 **먼저** 해야 합니다. PostgreSQL 표와 Neo4j 그래프를 지우고 다시 만드니 백업을 확인하세요.

- [ ] `python -m pipeline check`
- [ ] `python -m pipeline build` (2단계 뒤 개체 목록 검토에서 Enter)
- [ ] 웹 서버의 `.pg_meta_cache.json` 삭제 (표 구조 캐시, 24시간 유지)
- [ ] 노드 하나를 열어 라벨이 `유관순 — 출처정보 <원본 식별자> 언급 (n번째)` 형태인지 확인

## 0-1. 전문가 검증 시험 실행 (저자가 평가자 A 역할)

섭외 전에 작업 흐름과 소요 시간을 직접 확인하는 단계입니다. **이 결과는 논문에 쓰지 않습니다.**

1. 10월 5일에 만든 옛 워크북을 옮깁니다. export는 기존 파일을 덮어쓰지 않습니다.
   `mkdir -p evaluation/annotation/old && mv evaluation/annotation/expert/* evaluation/annotation/old/`
2. `python -m evaluation expert export` → `evaluation/annotation/expert/annotation_A.xlsx`, `_B.xlsx`
3. `annotation_A.xlsx`를 열어 [안내] 시트대로 일부(예: 기록 20건)를 판정하고, 걸린 시간을 잽니다.
4. `python -m evaluation expert score` → A의 점수가 계산되는지 확인 (B는 비어 있어 '미판정'으로만 나옴)
5. 시험이 끝나면 `annotation/expert/` 전체를 `annotation/trial/`로 옮기고, 실제 평가 전에 2번을 다시 실행합니다.
   같은 seed라 표본은 같고, 빈 워크북이 새로 만들어집니다.

## 1. 그래프와 원자료 수치 (DB 필요)

- [ ] `python -m pipeline inspect sources` → 4.1절 수치와 각주 (현재 값과 같으면 그대로)
- [ ] `python -m pipeline inspect counts` → 표 3·4 (그래프 노드·관계 수)
- [ ] `python -m pipeline inspect sparsity` → 4.5절의 53.6%, 부록 A의 고립 노드 2개

## 2. 벤치마크

- [ ] `python -m evaluation dataset --generate` → `dataset/benchmark.json`, `dataset/validation/benchmark_table.tex`
- [ ] 논문 표 2를 `benchmark_table.tex`로 교체하고 `\tbd` 표시 삭제
- [ ] `dataset/validation/`의 검토 시트에서 30% 표본을 직접 검토 (6.1절)

## 3. 평가 실행 (RQ1, RQ2)

안전성 보완 후에는 기존 결과와 섞지 않고 새 실행을 시작합니다. 수치로 정한 공통 생성 조건은
유지되지만, 실패 판정·문장별 부정 처리·근거 항목 분리·문맥 평균 정밀도 계산이 수정되었습니다.

- [ ] `data/output/build_manifest.json`의 `build_id` 확인. 기존 DB에 매니페스트가 없으면 고정된 스냅샷 이름을 `EVAL_DATA_BUILD_ID`로 지정
- [ ] PHP 서버는 매니페스트의 `build_id`를 자동으로 씁니다 (매니페스트가 없는 DB만 `DOCENT_DATA_BUILD_ID` 지정). 평가 중 PostgreSQL·Neo4j 변경 중지
- [ ] `python -B -m unittest discover -s tests -v`, `php -n tests/gemini_safety.php` 통과 확인
- [ ] `--resume`은 코드·데이터셋·패키지·모델·서버 설정·빌드 ID가 같을 때만 사용 (옛 실행은 재개 불가)

- [ ] 다른 터미널에서 `PHP_CLI_SERVER_WORKERS=4 DOCENT_EVAL_MODE=1 php -S localhost:8000`, `DOCENT_URL` 설정
- [ ] `python -m evaluation run --quick` (세 문항 점검)
- [ ] 10월 27일 1차 실행은 파일럿으로만 씁니다 (실행 중 `actions.php` 변경, 기준선이 서지정보만 검색, 채점 v1).
      `python -m evaluation report --rescore 2026-10-27_093400_076070`로 채점 v2 결과만 확인합니다.
- [ ] 기준선이 세 테이블(사건정보·출처정보·서지정보)을 검색하므로 하이브리드 기준선의 밀집 색인을 처음 한 번 다시 만듭니다
      (Ollama `nomic-embed-text`, 첫 실행에서 자동).
- [ ] `--limit 10`으로 시범 실행: 생성 실패가 없는지, 실패하면 answers.json의 `[Error: …]`에 서버 오류 내용이 남는지 확인
- [ ] `PHP_CLI_SERVER_WORKERS=4`를 빼면 `php -S`가 요청을 하나씩만 처리해, 웹 화면처럼 동시에 보내는 서사 요청이 다른 요청 뒤에서 기다리다 20초 제한에 걸립니다
- [ ] 평가 중에는 코드를 고치지 않습니다 (`php -S`는 요청마다 파일을 다시 읽으므로 실행 중 수정이 그대로 섞입니다)
- [ ] 10월 28일 2차 실행(`2026-10-28_132047_939870`)도 시범 실행입니다. 그 뒤 사건명 경로, 서사 진술의 기록 ID 인용,
      재순위화 CPU 실행이 들어갔습니다 (논문 평가 절의 '시범 실행' 문단).
- [ ] 10월 29일 3차 실행(`2026-10-29_071105_252397`)도 시범 실행입니다. 그 뒤 사건명 진술을 서사 맨 앞에 두고 한자 이름에 독음을 붙였습니다.
- [ ] curated 기준 사실: `evaluation/dataset/validation/curated_review.xlsx`에서 채택·수정·삭제를 정한 뒤 benchmark.json에 반영
- [ ] `python -m evaluation run` 2회 (각각 결과 폴더가 생김)
- [ ] `python -m evaluation report RUN1 RUN2` → `report.md`, `table_main.tex`, `table_by_category.tex`
- [ ] `python -m evaluation judge` (문맥 정밀도, Ollama 필요)
- [ ] `config.json`의 `model_versions`를 4.5절·부록 A에 기록 (날짜가 붙은 모델 버전)

## 4. 저자가 할 분석

- [ ] `error_analysis.xlsx`: D-CENT 답변 20개를 네 가지 실패 유형으로 부호화 (6.6절)
- [ ] `case_study.md`에서 사례 1건을 골라 6.6절에 수록

## 5. 역사학자 (섭외 후)

### 보내기 전 확인 (10월 24일 모의 실험으로 점검한 항목)

- [ ] 그래프 재구축 뒤에 `expert export`를 했는가 (기록 번호가 바뀌면 표본도 바뀜)
- [ ] `annotation_A.xlsx`의 records 시트에서 원문이 `열이름: 값` 줄로 보이고, 값이 열 이름과 맞는가 (예: `사건일자: 1919-…`)
- [ ] 기록 150건(사건정보 30, 출처정보 60, 서지 30, 세부장소 30), 항목 약 380개인가
- [ ] 답변 평가는 평가 실행 뒤 `rate export RUN --n 40`. 답변 속 인용이 `[근거 n]`으로 바뀌어 있고 `raw_` 식별자가 남지 않았는가
- [ ] 평가자에게 보내는 것: `annotation_A/B.xlsx`(또는 `rating_A/B.xlsx`) 한 개씩과 `ANNOTATION_GUIDE_ko.md`. `rating_key/`와 모의 결과(`sim*/`)는 보내지 않음
- [ ] 본 작업 전 보정: 두 평가자가 기록 10건 정도를 함께 판정하며 "놓친 이름으로 적는 기준"을 맞춤 (이 10건은 일치도 계산에서 제외)
- [ ] 받은 뒤 `expert score` / `rate score`의 "해석할 수 없는 값" 목록이 비어 있는지 확인하고, 있으면 평가자에게 해당 칸을 물어봄


- [ ] 그래프 검증: `expert export` → 두 분께 `annotation_A/B.xlsx`와 안내서 전달 → `expert score`
- [ ] 답변 평가: `rate export RUN --n 40` → `annotation/rating/`만 전달 (`rating_key/`는 전달 금지) → `rate score`

## 6. 결과가 나온 뒤 고칠 곳 (v9와 -ko 둘 다)

- [ ] 초록: "프로토콜을 정의한다"에서 끝나는 문장을 결과 요약으로
- [ ] 7장 결과, 9장 결론 요약 (`\tbd`)
- [ ] 8.1 시사점: 결과가 뒷받침하지 않는 항목은 고치거나 삭제
- [ ] 6.1절: 외부 질문을 받았으면 "이번 판에는 포함되지 않았다" 문장 수정
- [ ] 표 4와 6.1절 비율 문장: 지금은 105문항(저자 13, 템플릿 92) 기준. curated 문항을 바꾸거나 빼거나 `dataset --generate`를 다시 하면 숫자 갱신

## 7. 제출 전 확인

- [ ] **이용 조건**: 확인된 것은 사건 대장·출처 항목(이용 제한 없음), 사건–세부장소 연결(공공누리 제1유형).
      서지·세부장소·기구·행정구역·시소러스는 공공데이터포털에서 확인 후 Data Availability 문단의 `% TODO` 해소.
      국사편찬위원회 자료 일부는 공공누리 제4유형(변경금지)이므로 특히 확인.
- [ ] 참고문헌 `nikh2021thesaurus`의 URL을 시소러스 데이터셋 페이지로 교체 (`% TODO`)
- [ ] JOCCH 투고 규정(분량, 심사 방식, 부록 허용) 확인: https://dl.acm.org/journal/jocch/author-guidelines
- [ ] 익명 저장소 갱신. anonymous.4open.science의 치환 단어에 다음을 넣을 것:
      `jo.gyungmin@gmail.com`, `gyungmin`, `tsar.kr`, `11e.kr`, `tsarkr`
      (`docent.html`, `static/docent.js` 푸터와 `includes/security.php` 허용 도메인에 들어 있음)
- [ ] `\tbd`가 남아 있지 않은지: `grep -n tbd Latex/march-1st-1919_v9.tex`
