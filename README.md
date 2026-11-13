# 미니 PJT: 피아노 독학 길잡이 (Piano Study Companion)

## 무엇을 푸나
레슨 없이 혼자 클래식 피아노를 배우는 성인 입문자(체르니·소나티네 수준)가 "어떻게 연습하나 · 다음에 뭘 치나 · 이 곡은 어떤 곡인가"를 물을 때, **지어낸 난이도나 일화 없이 Henle 난이도 등급과 출처를 근거로** 답하고, 사용자의 수준을 기억해 맞춤 추천하는 에이전트다.

- 서비스 스펙: [SERVICE.md](SERVICE.md)
- 1차 평가: [evaluation/round1_report.md](evaluation/round1_report.md)
- 2차 평가: [evaluation/round2_report.md](evaluation/round2_report.md)

## 활용한 패턴 (Day 1~7)

| # | 패턴 | Day | 적용 | 코드 |
|---|---|---|---|---|
| 1 | LCEL chain + Pydantic 구조화 출력 | 1 | 가드 분류(`GuardDecision`), 계획(`Plan`), 최종 답변 검수(`FinalAnswer`: answer·인용 doc_id·grounded)를 모두 구조화 출력으로 받는다. finalize 는 `ChatPromptTemplate \| llm.with_structured_output()` 체인 | `src/guard.py`, `src/planner.py`, `src/agent.py:310` |
| 2 | ReAct (도구 자율 선택) | 3 | `llm.bind_tools(TOOLS)` 에이전트가 도구 4종을 고르고 조합한다. planner 계획은 지시로만 주고 실행은 에이전트가 정한다 | `src/agent.py:252` |
| 3 | RAG (하이브리드·리랭킹·쿼리 확장) | 2 | Chroma(Titan Embed v2) + BM25(kiwipiepy 형태소) 앙상블, 질문 유사도 **리랭킹**, planner 가 요구 항목별 보조 검색어를 만드는 **쿼리 확장**(prefetch 노드가 먼저 실행) | `src/retriever.py`, `src/agent.py:171` |
| 4 | 도구 다중 결합 | 4 | 한 질문에 문서 검색 2종 + 곡 DB 조회·필터를 함께 쓴다. 예: "달빛 배경·난이도·연습 순서" → `search_music_knowledge` + `get_piece` + `search_technique` | `src/tools.py` |
| 6 | 가드레일 (인젝션·범위·저작권·건강) | 5 | 규칙 기반 인젝션 사전 차단 + LLM 분류 → answer / limited / refuse. 거절은 고정 문구, 건강·비클래식은 제한 답변 지침 | `src/guard.py` |
| 7 | HITL (승인) | 5 | 학습 프로필 저장 전에 `interrupt()` 로 멈추고 사용자 승인을 받은 경우에만 저장. 추출과 승인 대기를 별도 노드로 나눠, 재개할 때 LLM 추출이 다시 실행되지 않게 했다 | `src/agent.py:413`, `src/agent.py:433` |
| 8 | 미들웨어 (재시도) | 5 | Bedrock ThrottlingException 대비 boto3 적응형 재시도(최대 10회) | `src/config.py` |
| 10 | Plan-Execute · 장기 메모리 | 7 | **planner** 가 의도·필수 도구·체크리스트를 정하고 **check** 가 실제 호출과 대조한다. **LangGraph SqliteStore** 학습 프로필(수준·연습 중인 곡·끝낸 곡·목표)로, 이미 아는 정보는 되묻지 않는다. 대화 기록은 SQLite 체크포인터 | `src/planner.py`, `src/memory.py`, `src/agent.py:278` |
| 11 | Observability · Trace | 7 | 응답 `trace` 에 노드·도구 단계별 입출력을 기록한다. `.env` 에 LangSmith 키가 있으면 LangSmith 트레이싱 | `src/agent.py:549`, `src/config.py` |
| 12 | 평가 (RAGAS · LLM-as-Judge) | 7 | 20건 인-아웃 세트: LLM-as-judge 통과 판정 + 코드 도구 검사 + RAGAS 4지표. 대화형 5시나리오 평가, SERVICE.md 지표(난이도 환각·출처 표기율) 코드 측정 | `evaluation/` |

적용하지 않은 패턴과 이유:
- **#5 MCP**: 사내 시스템 연동 요구가 없는 개인 학습 도메인이라 적용하지 않았다.
- **#9 Supervisor**: 도구 4종·유스케이스 3개 규모에서는 이득보다 비용이 컸다. LLM 호출이 2~3배로 늘고, 요약 단계에서 faithfulness 가 떨어질 위험이 있다. 그래서 planner(Plan-Execute)를 택했다.

## 아키텍처

노드별 입출력까지 보이는 다이어그램: [`docs/architecture.html`](docs/architecture.html) (브라우저로 연다)

```
POST /query ─▶ guard ─┬─ refuse ─────────────────────────────────────────────────────────▶ 응답
                      └─ load_profile (장기 기억) ─┬─ planner ─┬─ ask_user (대화형 되묻기 턴) ─▶ 응답
                                                   │           └─ prefetch (쿼리 확장 검색)
                                                   │                 ↓
                                                   └─(limited)─▶ agent ⇄ tools (ReAct, 도구 4종)
                                                                     ↓
                                                   check (계획 대비 호출 검사, 빠지면 1회 재요청)
                                                                     ↓
                                                   finalize (리랭킹 + 검수, Pydantic)
                                                                     ↓ user_id 가 있으면
                                                   propose_memory → approve_memory ⏸ interrupt(승인) ─▶ 응답
```

| 구성 | 내용 |
|---|---|
| LLM | AWS Bedrock Claude Haiku 4.5 (`LLM_MODEL`, 기본 `global.` 추론 프로필) |
| 검색 | 연습법 인덱스 / 작곡가·작품·음악사 인덱스. `##` 섹션 단위 청킹, Chroma + BM25(kiwipiepy) 앙상블, Titan 임베딩 리랭킹 |
| 곡 DB | `data/repertoire.json` 50곡. 모든 등급은 henle.de 에서 확인한 G. Henle Verlag 난이도(1~9) |
| 코퍼스 | `data/docs/` 25개 문서. Wikipedia 요약(CC BY-SA, URL 표기) + "(자체 작성)" 표시 섹션 + 학습 단계↔Henle 대응 기준 |
| 도구 | `search_technique`, `search_music_knowledge`, `get_piece`, `find_pieces` |
| 기억 | `memory.sqlite` (LangGraph SqliteStore, 학습 프로필), `checkpoints.sqlite` (대화 상태) |
| API | FastAPI `POST /query`, `GET·DELETE /profile/{user_id}`, `/` 웹 채팅 화면 |

**호출 방식 두 가지**
- **단발형**(과제 규약): `{"question": "..."}`. 질문 하나에 답 하나다. 모호한 질문은 "되묻기 1줄 + 기본 경로"로 답한다.
- **대화형**: `{"question": "...", "thread_id": "t1", "user_id": "u1"}`
  - 대화 기록을 유지하고, 정보가 빠지면 되묻는다(`route=clarify`).
  - 사용자가 자기 수준을 말하면 저장 승인을 요청한다(`status=need_input`, `pending_action`).
  - 승인 응답은 `{"thread_id": "t1", "resume": "승인"}` 으로 보낸다.

응답은 과제 규약 `{answer, contexts:[{doc_id,text}], trace:[{step,input,output}]}` 에 `route`, `refusal_reason`, `status`, `thread_id`, `pending_action`, `profile`, `clarify_options` 를 더한 형태다.

## 실행 방법

**1. 환경 준비** (Python 3.14, Windows 기준)
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```
ragas 는 의존성 `scikit-network` 가 Python 3.14 용 wheel 이 없어 빌드에 실패한다. 아래처럼 그 의존성만 빼고 설치한다. 평가 스크립트가 ragas 의 vertexai import 를 빈 모듈로 보정한다(`evaluation/run_eval.py`).
```bash
.venv/Scripts/python -m pip install "numpy<3" "datasets>=4" tiktoken nest-asyncio appdirs "diskcache>=5.6.3" typer rich "openai>=1" tqdm instructor "pillow>=10.4" networkx langchain_openai
.venv/Scripts/python -m pip install --no-deps ragas==0.4.3
```

**2. `.env`** (커밋하지 않는다)
```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
LLM_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0
# 선택: LANGSMITH_TRACING=true, LANGSMITH_API_KEY=..., LANGSMITH_PROJECT=...
```

**3. 서버 실행 → 웹 화면**
```bash
.venv/Scripts/python -m uvicorn src.api:app --port 8000
```
또는 `bash run.sh` (서버), `bash run.sh eval` (인-아웃 세트 평가), `bash run.sh test` (pytest).
브라우저에서 http://localhost:8000 을 연다. 채팅, 되묻기 선택지 버튼, 프로필 저장 승인 카드, 근거·처리 과정 보기, 학습 프로필 조회·삭제를 할 수 있다. 첫 요청 때 Chroma 인덱스를 만든다.

**4. API 직접 호출**
```bash
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d "{\"question\": \"모차르트 소나타 K.545 난이도랑 미리 익혀둘 테크닉 알려줘\"}"
```

**5. 평가·테스트**
```bash
.venv/Scripts/python -m evaluation.run_eval --round 2 --tag myrun
.venv/Scripts/python -m evaluation.run_multiturn
.venv/Scripts/python -m evaluation.measure_service_metrics
.venv/Scripts/python -m pytest -q tests/
```
- `evaluation.run_eval`: 20건 인-아웃 세트 + RAGAS
- `evaluation.run_multiturn`: 대화형 5시나리오
- `evaluation.measure_service_metrics`: 난이도 환각·출처 표기율
- `pytest`: API 계약 5건 + 기억·승인 시나리오 8건

## RAGAS 평가 결과
2차는 v12 를 같은 코드로 3회 측정한 평균 ± 표준편차다. positive 8건 기준, judge 는 Haiku 4.5.

| 지표 | 1차 | 2차 | 목표 |
|---|---|---|---|
| context_recall | 0.81 | **0.71** ± 0.01 | ≥ 0.70 |
| context_precision | 0.72 | **0.84** ± 0.00 | ≥ 0.70 |
| faithfulness | 0.80 | **0.86** ± 0.01 | ≥ 0.80 |
| answer_relevancy | 0.70 | **0.70** ± 0.01 | ≥ 0.80 (미달) |

SERVICE.md 지표도 측정했다.
- **난이도 환각**: 1차·2차 3회 모두 0건이다. 답변의 "Henle 난이도 N"을 곡 DB 값과 대조했다.
- **출처 표기율**: 1차 92% → 2차 3회 모두 100%.

## 인-아웃 세트 통과율 (자체 평가)
- 1차: **14 / 20**. 수정된 평가기로 재채점한 값이다. 최초 채점은 11/20 이었는데, 차이 3건은 평가기 결함 때문이었다.
- 2차: **18.7 / 20**. 3회 측정(18·19·19), guardrail 3/3 은 3회 모두.
- 개선폭: **+4.7 건**. 1차 실패 6건 중 5건이 3회 모두 통과했다. 주요 개선 사항은 다음과 같다.
  - planner·check 로 필요한 도구를 빠뜨리지 않게 했다.
  - 요구 항목별 쿼리 확장으로 놓친 근거(예: 고전주의 대표 작곡가)를 찾게 했다.
  - 리랭킹으로 관련 근거를 앞에 두었다.
  - 되묻기 판단을 넣었다.
  - 추천 곡마다 Henle 등급을 쓰게 했다.
  - 진도↔Henle 대응 기준 문서를 추가했다.
- 대화형 5시나리오: 대화 동작 5/5, 최종 답 4/5.

## 트라이앤에러 회고

**시도했지만 실패한 접근**
- **평가기를 먼저 의심하지 않았다.** 1차 실패 9건 중 3건은 에이전트가 아니라 judge 결함이었다.
  - judge 가 contexts 없이 채점해 위키 출처를 "신뢰 불가"로 판정하고, DB 등급을 자기 지식으로 반박했다.
  - RAGAS 가 한국어 답에서 영어 질문을 역생성해 answer_relevancy 가 0.07 까지 떨어졌다.
  - 부분 근거를 0점 처리해 context_precision 이 실제보다 낮게 나왔다.
  - 세 결함을 고친 뒤 1·2차를 같은 기준으로 재채점해, 평가기 효과를 개선 폭에서 뺐다.
- **프롬프트로 지표 끌어올리기 (v4~v7).** answer_relevancy 를 올리려고 "핵심 답 먼저", "600자 제한", 체크리스트 분리, 적응형 길이를 차례로 넣었다.
  - 6~8건 표본에서는 좋아 보였다.
  - 20건 전체에서는 faithfulness 가 0.94→0.74 로 무너져 모두 되돌렸다.
  - 소표본 판단은 위험하고, 고정 길이 제한은 여러 항목을 묻는 질문에 맞지 않았다.
- **되묻기용 `interrupt()` (v8).** 재개하면 계획을 처음부터 다시 세워, 사실상 "새 질문 보내기"와 같았다. 한 번 묻고 끝나 이어지는 대화도 안 됐다.

**최종 채택한 접근과 이유**
- **HITL 은 사용자 데이터를 바꾸는 행동(프로필 저장)에만 쓴다.** 되묻기는 일반 대화 턴으로 처리하고, 장기 기억으로 이미 아는 정보는 묻지 않는다. 잘못 알아듣고 저장하면 이후 추천이 모두 틀어지므로, 사람 확인이 실제로 필요한 지점이다.
- **규칙이 빠지면 안 되는 곳은 코드로 강제한다.**
  - 필수 도구 호출 검사(check)
  - 쿼리 확장 검색 선실행(prefetch)
  - 추천 질문의 대응 기준 문서 자동 검색
  - 프롬프트로만 지시했을 때는 모델이 검색을 다른 인덱스로 보내거나 빼먹었다.
- **같은 코드를 3회 측정했다.**
  - RAGAS 는 회차 간 편차가 ±0.01 로 작아, 버전 간 지표 차이는 실제 효과였다.
  - 통과 건수는 18~19 로 흔들렸다.

**남은 한계 · 향후 개선 방향**
- **answer_relevancy 0.70 (목표 0.8 미달).** 구어체 질문과 격식체 역생성 질문을 Titan 임베딩이 다르게 본다. RAGAS 계산에만 다국어 임베딩을 쓰는 평가기 보정을 1·2차 동일 기준으로 검토한다.
- **v12 의 recall·answer_relevancy 하락.** v9 대비 −0.13, −0.08 이다. 대응 기준 문서 자동 검색을 "사용자가 진도를 말했을 때만"으로 좁혀 원인을 분리해 볼 계획이다.
- **인-아웃 세트 5건의 기대 항목 문제**(#9, #13, #15, #16, #17). 코퍼스보다 먼저 써서 근거로 만족할 수 없거나 주관적인 기준이 있다. 다음 버전에서는 변경 이력을 공개하고 1·2차를 재채점한다. 자세한 내용은 [round2_report §7](evaluation/round2_report.md).
- **진도↔Henle 대응 기준은 자체 작성 참고 기준이다.** 공식 대응표가 아니므로 사용자 피드백으로 보정해야 한다.
- **응답 시간 12초.** 질문당 LLM 호출이 최소 4회다.

## 핵심 코드 위치
- `src/agent.py:459` — 메인 에이전트 그래프 (노드·엣지 정의, 단발형 `graph` / 대화형 `chat_graph`)
- `src/agent.py:171` — planner 노드 (계획·쿼리 확장·추천 시 대응 기준 자동 검색)
- `src/agent.py:252` — ReAct 에이전트 / `src/agent.py:278` — check / `src/agent.py:353` — finalize(리랭킹 + 검수)
- `src/agent.py:413`, `src/agent.py:433` — 프로필 저장 제안·승인(HITL interrupt)
- `src/agent.py:549` — `run_query()` (단발형·대화형·승인 재개 진입점)
- `src/tools.py` — 도메인 도구 4종 (`search_technique`, `search_music_knowledge`, `get_piece`, `find_pieces`)
- `src/retriever.py` — RAG 파이프라인 (섹션 청킹, Chroma + BM25 앙상블, `rerank()`)
- `src/planner.py` — 계획 수립 (의도·must_cover·쿼리 확장·되묻기 판단)
- `src/guard.py` — 입력 가드레일 / `src/memory.py` — 장기 기억(학습 프로필)
- `src/api.py` — FastAPI 서버 / `src/static/index.html` — 웹 채팅 화면
- `evaluation/run_eval.py` — 인-아웃 세트·RAGAS 평가 / `evaluation/run_multiturn.py` — 대화형 평가
