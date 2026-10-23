# 논문 완성까지 남은 일 (저자용)

명령은 저장소 루트에서 `.venv3.14/bin/python -m ...`으로 실행합니다. 역사학자에게는 이 문서가 아니라
[ANNOTATION_GUIDE_ko.md](ANNOTATION_GUIDE_ko.md)와 워크북만 전달합니다.

## 0. 전문가 검증 시험 실행 (저자가 평가자 A 역할)

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

- [ ] 다른 터미널에서 `DOCENT_EVAL_MODE=1 php -S localhost:8000`, `DOCENT_URL` 설정
- [ ] `python -m evaluation run --quick` (세 문항 점검)
- [ ] `python -m evaluation run` 2회 (각각 결과 폴더가 생김)
- [ ] `python -m evaluation report RUN1 RUN2` → `report.md`, `table_main.tex`, `table_by_category.tex`
- [ ] `python -m evaluation judge` (문맥 정밀도, Ollama 필요)
- [ ] `config.json`의 `model_versions`를 4.5절·부록 A에 기록 (날짜가 붙은 모델 버전)

## 4. 저자가 할 분석

- [ ] `error_analysis.xlsx`: D-CENT 답변 20개를 네 가지 실패 유형으로 부호화 (6.6절)
- [ ] `case_study.md`에서 사례 1건을 골라 6.6절에 수록

## 5. 역사학자 (섭외 후)

- [ ] 그래프 검증: `expert export` → 두 분께 `annotation_A/B.xlsx`와 안내서 전달 → `expert score`
- [ ] 답변 평가: `rate export RUN --n 40` → `annotation/rating/`만 전달 (`rating_key/`는 전달 금지) → `rate score`

## 6. 결과가 나온 뒤 고칠 곳 (v9와 -ko 둘 다)

- [ ] 초록: "프로토콜을 정의한다"에서 끝나는 문장을 결과 요약으로
- [ ] 7장 결과, 9장 결론 요약 (`\tbd`)
- [ ] 8.1 시사점: 결과가 뒷받침하지 않는 항목은 고치거나 삭제
- [ ] 6.1절: 외부 질문을 받았으면 "이번 판에는 포함되지 않았다" 문장 수정

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
