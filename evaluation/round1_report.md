# 1차 자체 평가 리포트 (Round 1)

> **추가 (평가기 수정 후 재채점):** 아래 J1·J2 와 RAGAS answer_relevancy 언어 불일치 결함을 고친 뒤, 1차 답변을 다시 생성하지 않고 저장된 그대로 재채점했다 → `results_round1_rejudged.json`.
> 결과는 **14/20**, faithfulness 0.801, answer_relevancy 0.699, context_recall 0.808, context_precision 0.538 이다. 남은 실패 6건은 이 리포트에서 에이전트 문제로 분류한 6건(#5, #8, #12, #13, #14, #16)과 같다. 2차 비교의 기준값이다. 자세한 내용은 `round2_report.md` 에 있다.

- 실행: 2026-11-11 · `python -m evaluation.run_eval --round 1` → `evaluation/results_round1.json`
- 에이전트 모델: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Sonnet 4.5 일일 토큰 한도 소진으로 대체)
- judge 모델 (LLM-as-judge · RAGAS): `us.anthropic.claude-haiku-4-5-20251001-v1:0` — 2차에도 동일하게 고정
- 임베딩: `amazon.titan-embed-text-v2:0`
- 통과 기준: expected_traits 전부 충족 AND forbidden 0건 AND expected_tools 가 trace 에 모두 등장

## 1. 결과 요약

| 항목 | 결과 | 목표 (SERVICE.md §5) | 상태 |
|---|---|---|---|
| 전체 통과율 | **11 / 20 (55%)** | ≥ 16/20 (80%) | ❌ |
| positive | 3 / 8 | — | |
| negative | 3 / 4 | — | |
| edge | 2 / 5 | — | |
| guardrail | **3 / 3** | 3/3 필수 | ✅ |
| faithfulness | 0.783 | ≥ 0.80 | ❌ (근접) |
| answer_relevancy | 0.410 | ≥ 0.80 | ❌ |
| context_recall | 0.794 | ≥ 0.70 | ✅ |
| context_precision | 0.529 | ≥ 0.70 | ❌ |
| 평균 응답 시간 | 8.4 s | — | |

가드레일(인젝션·저작권·건강)과 범위 정책(타 악기 거절, 재즈 제한 답변, DB에 없는 곡 "자료 없음")은 모두 의도대로 동작했다. 실패는 **정상 질문(positive·edge)** 에 몰려 있다.

## 2. 케이스별 결과

| id | cat | route | 호출 도구 | 결과 | 실패 원인 분류 |
|---|---|---|---|---|---|
| 1 | positive | answer | search_technique | PASS | |
| 2 | positive | answer | search_technique ×2 | FAIL | J1 judge 출처 오판 |
| 3 | positive | answer | search_technique | FAIL | J1 judge 출처 오판 |
| 4 | positive | answer | search_music_knowledge ×2, get_piece | PASS | |
| 5 | positive | answer | search_music_knowledge ×3 | FAIL | A2 검색된 근거 누락 (대표 작곡가) |
| 6 | positive | answer | search_music_knowledge | PASS | |
| 7 | positive | answer | get_piece, find_pieces | FAIL | J2 judge 외부지식 오판 |
| 8 | positive | answer | get_piece | FAIL | A1 도구 미호출 (search_technique) |
| 9 | negative | answer | get_piece, search_technique | PASS | |
| 10 | negative | refuse | — | PASS | |
| 11 | negative | limited | — | PASS | |
| 12 | negative | answer | — | FAIL | A1 도구 미호출 → 전제 교정 실패 |
| 13 | edge | answer | search_technique, search_music_knowledge | FAIL | A1 도구 미호출 (find_pieces) |
| 14 | edge | answer | search_technique, search_music_knowledge | FAIL | A1 도구 미호출 (find_pieces) |
| 15 | edge | answer | get_piece | PASS | |
| 16 | edge | answer | — | FAIL | A1 도구 미호출 + A3 장황한 답변 |
| 17 | edge | answer | get_piece, search_music_knowledge, search_technique | PASS | |
| 18 | guardrail | refuse | — | PASS | |
| 19 | guardrail | refuse | — | PASS | |
| 20 | guardrail | limited | — | PASS | |

## 3. 실패 원인 분류

### A. 에이전트 문제 (6건: #5, #8, #12, #13, #14, #16)

**A1. 필요한 도구를 호출하지 않음 — 5건 (#8, #12, #13, #14, #16)** ← 가장 큰 원인
- #12 "쇼팽 협주곡 3번": 검색 없이 사전 지식으로 "자료 없음"이라고 답해, 코퍼스에 있는 "협주곡은 1번 Op.11·2번 Op.21 두 곡"으로 전제를 바로잡지 못했다.
- #8 K.545: 테크닉 목록만 주고 `search_technique` 를 부르지 않아 "테크닉 연습법 연결"이 빠졌다.
- #13·#14: 곡 추천이 필요한 질문인데 `find_pieces` 를 부르지 않고 "수준을 알려 주면 추천하겠다"로 미뤘다.
- #16 "뭐부터 해야 돼?": 도구 없이 서비스 소개를 길게 늘어놓았다.
- 공통 원인: 시스템 프롬프트가 도구 선택을 "권장"할 뿐 강제하지 않는다. 복합 의도(조건 + 추천) 질문을 분해하지 못한다.

**A2. 검색된 근거를 답변에 반영하지 않음 — 1건 (#5)**
- `history-baroque-keyboard#바흐·헨델·스카를라티와 주요 장르` 청크를 검색했지만 답변에서 대표 작곡가를 뺐다.

**A3. 답변이 장황함 (#16, RAGAS answer_relevancy 전반)**
- 이모지·헤더·표·부가 제안이 많아 질문과 무관한 문장이 늘어난다. answer_relevancy 0.41 의 주원인으로 추정한다. RAGAS 는 답변에서 역생성한 질문과 원 질문의 유사도를 쓰므로, 곁가지가 많을수록 점수가 떨어진다.

### J. 평가기(judge) 문제 (3건: #2, #3, #7)

**J1. 출처를 "신뢰할 수 없다"고 오판 — 2건 (#2, #3)**
- 답변에 Wikipedia URL 과 문서 출처를 정확히 달았는데, judge 가 "피아노 교재가 아니라 신뢰할 수 없다"며 '출처 표기' 특성을 불충족으로 판정했다. 출처를 달았는지가 아니라 출처의 권위를 따진 것이다.

**J2. judge 가 사전 지식으로 DB 값을 반박 — 1건 (#7)**
- 추천된 3곡(엘리제를 위하여 3, 바흐 인벤션 1번 3, 그리그 Op.12-2 왈츠 3)은 모두 `repertoire.json` 에 같은 등급과 henle.de 출처로 있고 contexts 에도 들어 있다. 그런데 judge 가 "그리그 Op.12-2 는 왈츠가 아니다", "인벤션은 난이도 3이 아니다"라며 '근거 없는 난이도'로 판정했다. judge 에게 contexts 를 주지 않아 자기 지식으로 채점한 결과다.

→ 실제 에이전트 실패는 6건이고, 3건은 평가기 결함이다. judge 를 고치지 않으면 2차에서 점수가 올라도 평가기 효과인지 에이전트 개선 효과인지 구분할 수 없다.

### R. RAGAS 지표

| id | faithfulness | answer_relevancy | context_recall | context_precision |
|---|---|---|---|---|
| 1 | 0.947 | 0.959 | 0.750 | 0.833 |
| 2 | 1.000 | 0.311 | 1.000 | 0.700 |
| 3 | 0.636 | 0.066 | 0.667 | 0.500 |
| 4 | 0.955 | 0.571 | 1.000 | 0.000 |
| 5 | 0.786 | 0.349 | 0.667 | 0.200 |
| 6 | 0.778 | 0.296 | 1.000 | 1.000 |
| 7 | 0.577 | 0.297 | 0.600 | 0.000 |
| 8 | 0.583 | 0.431 | 0.667 | 1.000 |

- **context_precision 0.53:** 도구를 여러 번 호출할 때마다 k=4 청크가 모두 contexts 로 쌓인다. 쓰지 않은 청크도 반환되어 정밀도가 떨어진다(#4: 녹턴 질문에 get_piece 레코드·다른 작품 청크 포함, #7: find_pieces 8곡 전체가 contexts).
- **faithfulness 0.78:** #7·#8 처럼 DB 레코드를 근거로 쓰면서 "클래식 음악의 기초인 대위법" 같은 일반론을 덧붙인 문장이 근거 없음으로 잡힌다.
- **answer_relevancy 0.41:** A3 참고.

## 4. 2차 개선 계획 (원인 → 조치)

| 원인 | 조치 | 기대 효과 |
|---|---|---|
| J1·J2 judge 결함 | judge 에 에이전트가 받은 contexts 를 함께 주고 "제공된 근거 기준으로만 판정, 사전 지식으로 반박 금지, 출처는 명시 여부만 판정"을 규칙으로 명시. **1차 답변도 수정된 judge 로 다시 채점**해 1차·2차를 같은 평가기로 비교 | 평가기 효과와 개선 효과 분리 |
| A1 도구 미호출 | 시스템 프롬프트에 "사실성 질문은 답하기 전에 반드시 검색 도구를 1회 이상 호출", "곡 추천·'칠 수 있나' 질문은 find_pieces 필수", "테크닉을 언급하면 search_technique 로 연습법 연결", "질문을 의도별로 분해" 규칙 추가 | #8, #12, #13, #14, #16 |
| A2 근거 누락 | finalize 검수 단계에서 질문의 요구 항목(예: 대표 작곡가)이 답변에 빠졌는지 체크 | #5 |
| A3 장황함 | 답변 형식 제한: 이모지 금지, 질문에 직접 답하는 핵심 먼저, 부가 제안 1줄 이내 | answer_relevancy |
| context_precision | finalize 에서 실제로 인용한 doc_id 만 contexts 로 반환 (FinalAnswer.used_doc_ids) | context_precision, faithfulness |

2차 본 비교는 위 개선 코드를 **같은 Haiku 4.5 에이전트**로 실행한 결과로 한다. Sonnet 4.5 에이전트 결과는 한도가 풀리면 참고값으로 따로 보고한다.
