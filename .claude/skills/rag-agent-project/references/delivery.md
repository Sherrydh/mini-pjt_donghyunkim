# 제출 전 정리와 배포

## 1. 채점 기준으로 셀프 점검
채점 항목(예: SERVICE.md 품질, 서비스 임팩트, 개발 완성도, 인-아웃 충족도·개선폭, 가드레일 준수)별로 현재 상태와 공백을 표로 만든다.
가장 흔한 공백: **SERVICE.md §5에 정의만 하고 측정하지 않은 지표** → 측정 스크립트로 채운다.

SERVICE.md를 날카롭게 하는 법:
- 문제를 한 문장으로.
- 대표 장면 하나(구체적 사용자 + 상황 + 지금은 어떻게 해결하는지 + 에이전트가 주는 차이).
- 측정 근거(통과율, 환각 0건, 출처율)를 가치 주장 옆에 붙인다.
- 차별점은 기능 이름이 아니라 사용자 체감("내 수준을 기억한다")으로.

## 2. 정리 체크리스트
- [ ] 회사명·사내 문구·개인 정보 grep (`grep -rIl "회사명" --exclude-dir=.venv --exclude-dir=.git .`)
- [ ] 쓰지 않는 참고 코드(`src/reference/` 등) 삭제
- [ ] 다이어그램은 최신본만 `docs/`에 남기고 README에서 링크
- [ ] 공식 결과만 `evaluation/`에, 버전별·무효·로그는 `evaluation/archive/`로 `git mv`
- [ ] 문서 속 옮긴 파일 경로를 `archive/`로 갱신(정규식으로 파일명 경계 지켜 치환), 리포트에 archive 안내 한 줄
- [ ] 측정 스크립트 기본 입력 파일이 아직 존재하는지
- [ ] README 코드 위치 줄 번호 재대조
- [ ] `run.sh`(serve/eval/multi/test), `.gitattributes`로 LF 고정, `bash -n` 문법 확인
- [ ] requirements.txt 헤더·누락 패키지(pytest, httpx, streamlit 등)
- [ ] `.env`, `*.sqlite`, `chroma_db/`, `.streamlit/secrets.toml`이 .gitignore에 있고 추적되지 않는지 (`git ls-files | grep -i env`)
- [ ] pytest 전체 통과(실제 API 호출이면 4~5분 걸림 — 타임아웃 넉넉히)
- [ ] 커밋은 사용자에게 확인 후

## 3. 제출용 짧은 설명 (5줄 형식)
1. 누구의 어떤 문제를, 무엇을 근거로 답하는 에이전트인지
2. 그래프 흐름 한 줄 (가드레일 → 기억 → planner → ReAct → 리랭킹·검수, HITL 승인)
3. 정책: 되묻기, 범위 밖·저작권·건강·인젝션 처리, 지어내지 않음
4. 수치: 1차 → 2차 통과율, guardrail, 도메인 지표, 대표 RAGAS
5. 실행: API·화면·run.sh

## 4. 배포 — Streamlit Community Cloud (처음 배포하는 사용자용)

"스트림덱" 등으로 잘못 들은 용어는 대개 Streamlit이다. 배포 개념(로컬 → 인터넷 주소)부터 짧게 설명한다.

선택지:
| 방법 | 장점 | 단점 |
|---|---|---|
| Streamlit Cloud | 무료, GitHub 연결 후 버튼 | 화면만, 과제 API(`POST /query`)는 공개 안 됨 |
| Render/Railway/HF Spaces | FastAPI 그대로 | 설정·Docker 필요 |

`streamlit_app.py` 작성 요령:
- 맨 앞에서 `st.secrets` → `os.environ` 복사(설정 모듈이 import 시 env를 읽으므로 import 전에).
- `@st.cache_resource`로 에이전트 import + 검색 인덱스 미리 생성.
- 세션 상태: user_id, thread_id, history, queued 요청, 답한 제안 ID 집합, 질문 수.
- 버튼은 `on_click` 콜백으로 queued에 넣고, 렌더 후 한 곳에서 처리 → `st.rerun()`.
- 승인 카드: 마지막 메시지만 활성, 이전 카드는 "만료됨", 클릭 시 proposal_id 전송.
- 요금 보호: `APP_PASSWORD`, 세션당 질문 수 제한.
- 로컬 확인: `.claude/launch.json`에 streamlit 구성을 추가하고 브라우저 프리뷰로 실제 클릭 확인.

사용자에게 안내할 절차:
1. push (Streamlit Cloud는 로컬이 아니라 GitHub에서 읽는다 — "화면 파일 어디서 만들어?" 질문이 나온다)
2. share.streamlit.io → GitHub 로그인 → **Authorize streamlit만 누르고 Organization access의 Grant/Request는 누르지 않기**(개인 저장소면 불필요, 회사·교육 조직 권한 노출 방지)
3. Create app → 저장소/브랜치/`streamlit_app.py` → Advanced settings에서 Python 3.12 + Secrets(AWS 키, LLM_MODEL, APP_PASSWORD)
4. Deploy → 첫 접속 1~2분(인덱스 생성)

주의: 질문마다 본인 AWS 요금, 무료 서버 재시작 시 sqlite·chroma 초기화, 로컬과 다른 Python 버전 호환성, 시연 후 앱 삭제.
"Streamlit은 프론트 코드가 없는데 화면이 되나?" → 프론트(React)는 라이브러리에 내장, 파이썬은 배치만 기술하고 상호작용마다 스크립트가 재실행된다고 설명.
