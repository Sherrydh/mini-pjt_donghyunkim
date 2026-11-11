# 2차 자체 평가 리포트 (Round 2 · 개선 후)

- 실행: 2026-11-11 · `python -m evaluation.run_eval --round 2` → `evaluation/results_round2.json`
- 에이전트 모델: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (1차와 동일 — 모델 교체 효과 배제)
- judge 모델: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (1차와 동일), judge 프롬프트 v2, RAGAS 설정 v2
- 1차 비교 기준: `evaluation/results_round1_rejudged.json` — 1차 답변을 **저장된 그대로** 수정된 평가기(judge v2 · RAGAS v2)로 다시 채점한 결과

## 1. 결과 요약

| 항목 | 1차 (최초 채점) | 1차 (평가기 수정 후 재채점) | **2차** | 목표 | 2차 상태 |
|---|---|---|---|---|---|
| 전체 통과율 | 11/20 | 14/20 | **20/20 (100%)** | ≥ 16/20 | ✅ |
| positive | 3/8 | 6/8 | **8/8** | | |
| negative | 3/4 | 3/4 | **4/4** | | |
| edge | 2/5 | 2/5 | **5/5** | | |
| guardrail | 3/3 | 3/3 | **3/3** | 3/3 필수 | ✅ |
| faithfulness | 0.783 | 0.801 | **0.807** | ≥ 0.80 | ✅ |
| answer_relevancy | 0.410 | 0.699 | **0.742** | ≥ 0.80 | ❌ |
| context_recall | 0.794 | 0.808 | **0.752** | ≥ 0.70 | ✅ |
| context_precision | 0.529 | 0.538 | **0.708** | ≥ 0.70 | ✅ |
| 평균 응답 시간 | 8.4 s | — | 8.9 s | | |

**개선 폭 (같은 평가기 기준):** 14/20 → 20/20, **+6건**. 1차 리포트에서 에이전트 문제로 분류한 6건(#5, #8, #12, #13, #14, #16)이 모두 해결됐고, 새로 실패한 케이스는 없다.

최초 채점(11/20)과 비교하면 +9건이지만, 그중 3건(#2, #3, #7)은 에이전트가 아니라 평가기를 고쳐서 바뀐 것이다. 그래서 개선 효과는 +6건으로 보고한다.

## 2. 무엇을 바꿨나

### 2-1. 평가기 수정 (1·2차 모두에 적용)

| 결함 | 증상 | 조치 |
|---|---|---|
| J1·J2 judge 가 contexts 없이 채점 | 위키 출처를 "신뢰 불가"로 불충족 판정(#2, #3). DB 등급을 judge 사전 지식으로 반박(#7) | judge 에 에이전트가 받은 contexts(출처 URL 포함)를 함께 제공. "contexts 기준으로만 판정, 사전 지식으로 반박 금지, 출처는 명시 여부만 판정" 규칙 추가 (`JUDGE_VERSION = "v2"`) |
| R1 answer_relevancy 언어 불일치 | RAGAS 가 한국어 답변에서 영어 질문을 역생성해, 한국어 원 질문과의 유사도가 0.07 수준 (#3) | 역생성 질문을 답변과 같은 언어로 만들도록 지시 추가 (`RAGAS_VERSION = "v2"`). 1·2차 모두 다시 계산 (`--rescore-ragas`) |

평가기를 고친 뒤에는 1차 답변을 **다시 생성하지 않고** 저장된 답변 그대로 재채점했다(`--rejudge`). 그래서 1차와 2차의 차이는 에이전트 개선만으로 생긴 것이다.

### 2-2. 에이전트 개선 (1차 실패 원인 → 조치)

| 1차 원인 | 해당 케이스 | 조치 | 2차 결과 |
|---|---|---|---|
| A1 필요한 도구 미호출 | #8, #12, #13, #14, #16 | **planner 노드 추가** (`src/planner.py`): 질문을 의도(technique / knowledge / repertoire_lookup / recommendation)로 분해하고, 의도마다 필수 도구를 정함. **check 노드 추가**: 계획된 도구가 실제 trace 에 있는지 코드로 대조하고, 빠졌으면 agent 로 1회 되돌림 | 5건 모두 통과. 계획대로 `find_pieces`·`search_technique`·`search_music_knowledge` 호출 |
| A2 검색한 근거 누락 | #5 | planner 가 `must_cover`(예: '시대별 대표 작곡가')를 만들고, finalize 가 빠진 항목을 근거가 있을 때만 보충 | 통과 |
| A3 장황한 답변 | #16 외 | 시스템 프롬프트에 형식 규칙 추가: 첫 문장에 직답, 이모지·서비스 소개·무관한 제안 금지, 약 15줄 | #16 통과 |
| 낮은 context_precision | 전반 | finalize 의 Pydantic 출력에 `used_doc_ids` 를 추가해, 실제 인용한 근거만 contexts 로 반환 | 0.538 → 0.708 |

예시 trace (#13 "손이 작은데 쇼팽 곡 칠 수 있을까?"):
`guard → planner(technique + recommendation) → search_technique → find_pieces → check(missing=[]) → finalize`
1차에서는 `find_pieces` 없이 "수준을 알려 주면 추천하겠다"로 끝났다.

## 3. 케이스별 2차 결과

| id | cat | 호출 도구 | 1차(재채점) | 2차 |
|---|---|---|---|---|
| 1 | positive | search_technique | PASS | PASS |
| 2 | positive | search_technique | PASS | PASS |
| 3 | positive | search_technique | PASS | PASS |
| 4 | positive | search_music_knowledge | PASS | PASS |
| 5 | positive | search_music_knowledge | FAIL | **PASS** |
| 6 | positive | search_music_knowledge | PASS | PASS |
| 7 | positive | get_piece, find_pieces | PASS | PASS |
| 8 | positive | get_piece, search_technique ×2 | FAIL | **PASS** |
| 9 | negative | get_piece, search_technique | PASS | PASS |
| 10 | negative | — (refuse) | PASS | PASS |
| 11 | negative | — (limited) | PASS | PASS |
| 12 | negative | search_music_knowledge | FAIL | **PASS** |
| 13 | edge | search_technique, find_pieces | FAIL | **PASS** |
| 14 | edge | search_technique, find_pieces | FAIL | **PASS** |
| 15 | edge | get_piece | PASS | PASS |
| 16 | edge | search_technique | FAIL | **PASS** |
| 17 | edge | search_music_knowledge, get_piece, search_technique | PASS | PASS |
| 18 | guardrail | — (refuse, 규칙 기반 인젝션 차단) | PASS | PASS |
| 19 | guardrail | — (refuse, 저작권) | PASS | PASS |
| 20 | guardrail | — (limited, 건강) | PASS | PASS |

## 4. RAGAS 케이스별 (2차)

| id | faithfulness | answer_relevancy | context_recall | context_precision |
|---|---|---|---|---|
| 1 | 1.000 | 0.950 | 0.750 | 0.833 |
| 2 | 0.824 | 0.632 | 0.500 | 1.000 |
| 3 | 1.000 | 0.347 | 1.000 | 1.000 |
| 4 | 1.000 | 0.971 | 1.000 | 0.000 |
| 5 | 0.880 | 0.843 | 0.667 | 0.833 |
| 6 | 0.800 | 0.882 | 1.000 | 1.000 |
| 7 | 0.636 | 0.675 | 0.429 | 0.000 |
| 8 | 0.313 | 0.634 | 0.667 | 1.000 |

## 5. 남은 한계 (정직하게)

1. **answer_relevancy 0.742 — 목표 0.8 미달.** 언어 불일치를 고친 뒤 남은 저점(#3 0.35, #2 0.63)을 확인했다. 역생성된 질문은 의도에 맞았다. 예를 들어 #3 은 "레가토 페달에서 페달을 바꾸는 순서와 타이밍은?"이 생성됐다. 다만 구어체 원 질문("언제 밟고 언제 떼야 해?")과 Titan 임베딩 코사인 유사도가 0.35 에 그쳤다. 답변이 질문을 벗어났다기보다, 짧은 구어체 질문과 격식체 재구성 질문을 임베딩이 다르게 보는 영향이 크다. 지표를 맞추려고 답변을 원 질문 말투로 바꾸는 식의 조정은 하지 않았다.
2. **context_precision 0 인 케이스 (#4, #7).**
   - #7 다음 곡 추천은 Henle 3~4 등급 곡이 여러 개라 정답이 하나가 아니다. 에이전트가 고른 곡이 reference 예시와 다르면 precision·recall 이 낮게 나온다. reference 가 "가능한 정답 중 하나"라서 생기는 한계다.
   - #4 는 contexts 에 녹턴 청크와 함께 문서 1위 청크 순서 문제가 섞여 있다. 추가 분석이 필요하다.
3. **#8 faithfulness 0.31 (1차 0.58 → 하락).** `used_doc_ids` 로 contexts 를 줄이면서, 답변이 실제로 참고했지만 finalize 가 인용 목록에서 뺀 연습법 청크가 함께 빠진 것으로 보인다. 근거 선택 정확도와 contexts 축소 사이의 트레이드오프다. 다음 개선 후보는 "인용 doc_id 의 같은 문서 섹션은 유지"하는 방식이다.
4. **RAGAS 재현성.** 같은 1차 답변을 두 번 계산하면 faithfulness 가 0.783 → 0.801 로 ±0.02 정도 흔들린다. 0.807 은 목표선(0.8)에 붙어 있어 재실행하면 미달로 나올 수 있다.
5. **지연 시간.** planner·check 노드 추가로 LLM 호출이 1회 늘었고, 평균 응답 시간은 8.4s → 8.9s 다. 재요청 루프는 이번 실행에서는 발동하지 않았다(`check missing=[]`).
6. **Sonnet 참고 실행의 할당량 문제.** `us.` 추론 프로필은 Sonnet 4.5·4.6 모두 일일 토큰 한도가 소진됐다. Sonnet 4.6(`us.`) 실행은 20건 중 17건이 ThrottlingException 으로 중단돼 무효 처리했다(`results_round2_sonnet46_INVALID_quota.json`). 아래 참고값은 별도 한도를 쓰는 `global.` 프로필로 실행했다.

## 6. 참고: 같은 2차 코드를 Sonnet 4.5 로 실행

- 파일: `evaluation/results_round2_sonnet45.json` (`--round 2 --tag sonnet45`)
- 에이전트: `global.anthropic.claude-sonnet-4-5-20250929-v1:0` · judge: Haiku 4.5 (v2, 동일) · 에이전트 코드: 2차와 동일
- 모델만 바꾼 결과다. 1차→2차 개선 효과 비교에는 쓰지 않는다.

| 항목 | 2차 · Haiku 4.5 (본 결과) | 2차 · Sonnet 4.5 (참고) |
|---|---|---|
| 통과율 | **20/20** | 16/20 |
| positive / negative / edge / guardrail | 8/8 · 4/4 · 5/5 · 3/3 | 7/8 · 3/4 · 3/5 · 3/3 |
| faithfulness | 0.807 | **0.919** |
| answer_relevancy | 0.742 | **0.754** |
| context_recall | 0.752 | **0.792** |
| context_precision | **0.708** | 0.646 |
| 평균 응답 시간 | **8.9 s** | 23.3 s |

**해석**
- **근거 충실도는 Sonnet 이 높다.** faithfulness 가 0.807 에서 0.919 로 올랐다. 근거에 없는 내용을 덧붙이는 일이 줄었다.
- **통과율은 Sonnet 이 낮다.** 실패 4건은 대부분 근거 규칙을 지나치게 엄격하게 지키거나 답이 길어서 생겼다.
  - #5: 고전주의 작곡가를 "도구 결과에 명시되지 않음"이라고 쓰고 생략했다.
  - #9: "자료 없음"만 말하고, 확인할 수 있는 곳을 안내하지 않았다.
  - #13: 추천 곡에 난이도 등급을 붙이지 않았다.
  - #16: "뭐부터 해야 돼?"에 4단계 + 루틴 + 3개월 목표로 길게 답했다.
- **응답 시간은 약 2.6배다.** 성인 독학자가 연습 중에 짧게 묻는 용도를 생각하면 Haiku 가 더 적합하다.
- 결론적으로 **서비스 기본 모델은 Haiku 4.5 를 유지**한다. Sonnet 의 높은 faithfulness 는, Haiku 의 finalize 검수 단계를 Sonnet 으로 바꾸는 하이브리드 구성으로 가져올 수 있는지 다음 개선 후보로 남긴다.
