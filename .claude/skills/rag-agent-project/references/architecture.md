# 아키텍처 — 노드별 역할과 설계 결정

## 목차
1. 전체 그래프
2. 노드별 입출력
3. 구조 선택: Supervisor vs Planner
4. 장기 기억 + HITL 승인
5. 단발형 vs 대화형
6. 화면(프론트) 두 가지

## 1. 전체 그래프

```
POST /query ─▶ guard ─┬─ refuse ────────────────────────────▶ 응답
                      └─ load_profile ─┬─ planner ─┬─ ask_user (대화형 되묻기 턴) ─▶ 응답
                                       │           └─ prefetch (보조 검색 강제 실행)
                                       └─(limited)─▶ agent ⇄ tools (ReAct)
                                                      ↓
                                       check (계획 대비 도구 호출 대조, 빠지면 1회 재요청)
                                                      ↓
                                       finalize (리랭킹 + 검수, Pydantic 구조화 출력)
                                                      ↓ user_id 가 있을 때
                                       propose_memory → approve_memory ⏸ interrupt ─▶ 응답
```

- 단발형 `graph`(체크포인터 없음)와 대화형 `chat_graph`(SqliteSaver)를 같은 builder로 두 번 compile 한다.
- 장기 기억은 LangGraph SqliteStore(`memory.sqlite`), 대화 상태는 `checkpoints.sqlite`. 둘 다 .gitignore.

## 2. 노드별 입출력

| 노드 | 입력 | 출력 | 메모 |
|---|---|---|---|
| guard | 질문 | route(answer/limited/refuse), category | 규칙 기반 인젝션 사전 필터 + LLM 구조화 분류 |
| load_profile | user_id | profile | 없으면 {} |
| planner | 질문 + 프로필 | intents, must_cover, coverage_queries(≤4), clarify, missing, clarify_options | Pydantic Plan |
| ask_user | plan | 되묻는 문장 + 선택지 | 대화형에서만 별도 턴, 단발형은 "되묻기 1줄 + 기본 경로" |
| prefetch | coverage_queries | 검색 결과(ToolNode) | 에이전트가 빼먹던 검색을 코드로 강제 |
| agent ⇄ tools | 메시지 | tool_calls / 답 초안 | bind_tools 로 도메인 도구 결합 |
| check | 호출된 도구 vs 계획 | 누락 시 재요청 1회 | 무한 루프 방지용 check_retries |
| finalize | 초안 + 근거 | answer, used_doc_ids | 리랭킹 순서를 그대로 API contexts 에 적용, 인용한 근거만 반환 |
| propose_memory | 이번 발화 + 직전 에이전트 질문 | proposed_profile, proposal_id | 저장은 하지 않는다 |
| approve_memory | interrupt | saved/rejected | 재개 시 노드가 처음부터 재실행되므로 LLM 호출 없이 interrupt 만 둔다 |

설계 교훈:
- 사용자가 노드 구조를 이해하기 어려워하면 **실제 케이스 하나로 노드마다 입력·출력 값을 보여 준다.** 다이어그램보다 효과가 컸다.
- LLM이 "검색해야 할 것"을 빼먹는 문제는 프롬프트로 강제하기보다 **planner가 계획 → 코드가 실행(prefetch) → check가 대조**하는 편이 안정적이었다.
- planner가 특정 문서를 엉뚱한 인덱스로 검색하면, 의도가 감지될 때 코드가 해당 검색을 자동 추가하게 한다.
- 리랭킹은 근거가 0~1건일 때 점수 키가 빠지지 않게 기본값을 준다(KeyError 경험).

## 3. 구조 선택: Supervisor vs Planner

사용자가 Supervisor 멀티에이전트를 제안했을 때 고려한 것:
- 실패 원인은 "필요한 도구를 안 부름"이었다. 서브 에이전트로 쪼개도 이 원인은 그대로다.
- 에이전트가 늘수록 호출 비용·지연이 늘고, 요약 전달 과정에서 faithfulness가 떨어질 위험이 있다.
- 1차·2차 비교 시 구조 변화 효과와 평가 효과가 섞인다.
→ 단일 에이전트 + planner 노드를 권했고 효과가 있었다(11/20 → 20/20, 같은 평가기 기준).
실패 원인을 먼저 보고, 그 원인을 직접 겨냥하는 최소 구조를 고른다.

## 4. 장기 기억 + HITL 승인

처음엔 HITL을 "모호한 질문 되묻기"에 썼다가 바꿨다. 이유:
- interrupt 후 재개하면 노드가 처음부터 다시 실행된다(계획을 다시 짬).
- 한 번 묻고 끝나며, 다음 질문에서 또 묻는다(기억이 없음).
- 되묻기는 그냥 대화 턴으로 처리하면 충분하다.

바꾼 구조:
- 되묻기 = 일반 대화 턴(ask_user). 되묻기에 interrupt를 쓰지 않는다고 분명히 권한다("둘 다 interrupt로 하면 일관성 있다"는 절충은 위 단점을 그대로 남긴다).
  되묻기는 1회까지, 그래도 모호하면 가장 흔한 해석으로 답하고 가정을 명시한다.
- HITL = **되돌리기 어려운 쓰기(프로필 저장) 승인**에만. 제안(propose)과 승인(approve)을 별도 노드로 분리.
- 프로필 추출 때 **직전 에이전트 질문을 함께 넘긴다**("K.545요"가 연습 중인지 끝낸 곡인지 판별).

만료된 승인 처리(나중에 발견한 버그):
- 승인 대기 중에 새 질문이 오면 LangGraph는 대기 중이던 interrupt를 버리고 새 턴을 시작한다.
- 옛 카드의 "승인"은 `Command(resume=...)`만 보내므로 ① 대기 제안이 없으면 직전 답을 그대로 반환하고,
  ② 새 제안이 있으면 그걸 승인해 버린다.
- 수정: interrupt payload 에 `proposal_id`, resume 전에 `graph.get_state(config).tasks[*].interrupts`로 대기 제안 확인,
  없거나 ID 불일치면 `route="expired"`로 저장 없이 응답, 화면은 새 질문 전송 시 이전 카드 비활성화.
- 이런 LangGraph 의미론은 추측하지 말고 **Bedrock 없이 작은 장난감 그래프로 재현**해 확인한다.

## 5. 단발형 vs 대화형

- 과제 규약 평가는 단발형(`{question}`)이라 되물어도 답해 줄 사람이 없다. 단발형은 "되묻기 1줄 + 기본 답"으로 처리한다.
- 대화 기능(되묻기 턴, 기억, 승인)은 별도 **대화형 평가**(스크립트 시나리오)와 pytest로 검증한다.
- 결과 보고 때 ① 인-아웃 세트, ② 대화형 평가, ③ 자동 테스트를 구분해 적는다(섞으면 "20건은 어디 갔어?" 질문이 나온다).

## 6. 화면 두 가지

| | 정적 HTML (FastAPI StaticFiles) | Streamlit |
|---|---|---|
| 코드 | HTML·CSS·JS 직접 작성 | 파이썬만 (프론트는 라이브러리가 제공) |
| 호출 | 브라우저 → POST /query | `run_query()` 직접 호출 |
| 용도 | 과제 API 확인, 세밀한 UI | 무료 배포(Streamlit Cloud) |

- 선택지 버튼은 답변 텍스트를 파싱하지 말고 API에 `clarify_options` 같은 구조화 필드로 준다(중복 버그 경험).
- 화면은 브라우저 프리뷰로 실제로 눌러 확인한다(질문 → 저장 카드 → 저장 → 사이드바 반영). 테스트용 저장 데이터는 지운다.
