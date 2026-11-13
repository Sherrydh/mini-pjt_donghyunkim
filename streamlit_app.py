"""피아노 독학 길잡이 — Streamlit 배포용 채팅 화면.

로컬 실행:   streamlit run streamlit_app.py
클라우드:     Streamlit Community Cloud 에서 이 파일을 Main file 로 지정 (README '배포' 참고)

에이전트 코드(src/)는 그대로 쓰고, FastAPI 대신 run_query() 를 직접 호출한다.
비밀값은 로컬은 .env, 클라우드는 Streamlit Secrets 에서 읽는다.
"""
import os
import uuid

import streamlit as st

# ---------- 비밀값: Streamlit Secrets → 환경 변수 (src.config 가 import 될 때 읽으므로 먼저 옮긴다) ----------
SECRET_KEYS = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION",
    "LLM_MODEL", "EMBED_MODEL", "LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT",
    "APP_PASSWORD", "MAX_QUESTIONS_PER_SESSION",
)
try:
    for _k in SECRET_KEYS:
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:  # secrets.toml 이 없으면 (.env 로 로컬 실행) 그냥 넘어간다
    pass

st.set_page_config(page_title="피아노 독학 길잡이", page_icon="🎹", layout="centered")

# ---------- 접근 제한: 공개 주소라 질문마다 AWS 요금이 나가므로 비밀번호·질문 수 제한을 둔다 ----------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
MAX_QUESTIONS = int(os.environ.get("MAX_QUESTIONS_PER_SESSION", "30"))

if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("🎹 피아노 독학 길잡이")
    pw = st.text_input("접속 비밀번호", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.authed = True
            st.rerun()
        st.error("비밀번호가 맞지 않습니다.")
    st.stop()


@st.cache_resource(show_spinner="에이전트를 준비하는 중입니다 (처음 한 번은 검색 인덱스를 만들어 1~2분 걸립니다)…")
def load_agent():
    from src import memory
    from src.agent import run_query
    from src.retriever import INDEXES, get_retriever

    for index in INDEXES:  # 첫 질문이 느려지지 않게 검색 인덱스를 미리 만든다
        get_retriever(index)
    return run_query, memory


run_query, memory = load_agent()

# ---------- 세션 상태 ----------
ss = st.session_state
ss.setdefault("user_id", "web-" + uuid.uuid4().hex[:8])
ss.setdefault("thread_id", ss.user_id + "-" + uuid.uuid4().hex[:6])
ss.setdefault("history", [])  # [{"role": "user", "text": ...} | {"role": "assistant", "res": {...}}]
ss.setdefault("queued", None)  # 처리할 요청: {"question": ...} 또는 {"resume": ..., "proposal_id": ..., "label": ...}
ss.setdefault("decided", set())  # 이미 답한 저장 제안 ID
ss.setdefault("asked", 0)

ROUTE_LABEL = {"clarify": "되묻기", "limited": "제한 답변", "refuse": "범위 밖", "expired": "만료된 제안"}
EXAMPLES = [
    "체르니 30번 치고 있는데 다음에 뭐 칠까?",
    "하농 연습을 효과적으로 하려면 어떻게 해야 해?",
    "모차르트 소나타 K.545 난이도랑 미리 익혀둘 테크닉 알려줘",
    "바로크 시대 건반 음악의 특징이 뭐야?",
]


def queue_question(q: str):
    ss.queued = {"question": q}


def queue_resume(word: str, proposal_id: str):
    ss.decided.add(proposal_id)
    ss.queued = {"resume": word, "proposal_id": proposal_id, "label": "저장할게요" if word == "승인" else "저장하지 않을게요"}


def describe_profile(p: dict) -> list[str]:
    rows = []
    if p.get("level"):
        rows.append(f"**수준**: {p['level']}")
    if p.get("current_pieces"):
        rows.append(f"**연습 중**: {', '.join(p['current_pieces'])}")
    if p.get("completed_pieces"):
        rows.append(f"**끝낸 곡**: {', '.join(p['completed_pieces'])}")
    if p.get("goal"):
        rows.append(f"**목표**: {p['goal']}")
    return rows


# ---------- 사이드바 ----------
with st.sidebar:
    st.subheader("내 학습 프로필")
    profile = memory.get_profile(ss.user_id) or {}
    rows = describe_profile(profile)
    if rows:
        for r in rows:
            st.markdown("- " + r)
        if st.button("프로필 삭제", use_container_width=True):
            memory.delete_profile(ss.user_id)
            st.rerun()
    else:
        st.caption("아직 저장된 정보가 없습니다. 대화 중에 수준이나 연습 곡을 말하면 저장 여부를 물어봅니다.")
    st.divider()
    if st.button("새 대화 시작", use_container_width=True):
        ss.thread_id = ss.user_id + "-" + uuid.uuid4().hex[:6]
        ss.history, ss.queued, ss.decided = [], None, set()
        st.rerun()
    st.caption(f"이번 세션 질문 {ss.asked} / {MAX_QUESTIONS}")
    st.caption("공개 자료(Wikipedia 요약 · Henle 난이도)를 근거로 답합니다. 레슨·진료를 대신하지 않습니다.")

# ---------- 본문 ----------
st.title("🎹 피아노 독학 길잡이")
st.caption("혼자 클래식 피아노를 배우는 성인을 위한 연습법 · 작곡가·작품 · 다음 곡 추천 도우미")

if not ss.history and not ss.queued:
    st.markdown("**이렇게 물어보세요**")
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
        cols[i % 2].button(ex, key=f"ex{i}", on_click=queue_question, args=(ex,), use_container_width=True)


def render_assistant(res: dict, idx: int, is_last: bool):
    with st.chat_message("assistant"):
        label = ROUTE_LABEL.get(res.get("route", ""))
        if label:
            st.caption(f"`{label}`")
        answer = res.get("answer", "")
        if res.get("pending_action"):  # 저장 확인 문구는 아래 카드로 보여 준다
            answer = answer.split("\n\n[학습 프로필 저장 확인]")[0]
        st.markdown(answer)

        if res.get("route") == "clarify" and is_last:
            opts = list(dict.fromkeys(o.strip() for o in res.get("clarify_options") or [] if o.strip()))
            if opts:
                cols = st.columns(min(len(opts), 3))
                for j, o in enumerate(opts):
                    cols[j % len(cols)].button(o, key=f"opt{idx}-{j}", on_click=queue_question, args=(o,))

        pending = res.get("pending_action")
        if pending and pending.get("type") == "save_profile":
            pid = pending.get("proposal_id", "")
            with st.container(border=True):
                st.markdown("**학습 프로필에 저장할까요?**")
                st.markdown(" · ".join(describe_profile(pending.get("proposed") or {})) or "(변경 없음)")
                if pid in ss.decided:
                    st.caption("답변 완료")
                elif not is_last:
                    st.caption("~~만료됨~~ — 새 질문을 보내 이 제안은 취소됐습니다. 저장하지 않았습니다.")
                else:
                    st.caption("저장하면 다음부터 이 정보에 맞춰 답합니다.")
                    c1, c2 = st.columns(2)
                    c1.button("저장", key=f"yes{idx}", type="primary", on_click=queue_resume, args=("승인", pid),
                              use_container_width=True)
                    c2.button("저장 안 함", key=f"no{idx}", on_click=queue_resume, args=("거절", pid),
                              use_container_width=True)

        if res.get("contexts"):
            with st.expander(f"근거 {len(res['contexts'])}건"):
                for c in res["contexts"]:
                    name = c["doc_id"].replace("repertoire:", "곡 DB: ")
                    src = c.get("source") or "자체 작성"
                    st.markdown(f"- {name} · [출처]({src})" if src.startswith("http") else f"- {name} · {src}")
        if res.get("trace"):
            with st.expander(f"처리 과정 {len(res['trace'])}단계"):
                for t in res["trace"]:
                    st.markdown(f"- `{t['step']}` {str(t.get('input', ''))[:90]}")


for i, item in enumerate(ss.history):
    if item["role"] == "user":
        with st.chat_message("user"):
            st.markdown(item["text"])
    else:
        render_assistant(item["res"], i, i == len(ss.history) - 1 and not ss.queued)

typed = st.chat_input("피아노 연습·작곡가·곡 추천에 대해 물어보세요")
if typed and typed.strip():
    queue_question(typed.strip())

# ---------- 요청 처리 ----------
if ss.queued:
    req, ss.queued = ss.queued, None
    if "question" in req and ss.asked >= MAX_QUESTIONS:
        st.warning(f"이 세션의 질문 한도({MAX_QUESTIONS}개)를 다 썼습니다. 페이지를 새로 고치면 다시 시작합니다.")
        st.stop()
    shown = req.get("question") or req.get("label")
    ss.history.append({"role": "user", "text": shown})
    with st.chat_message("user"):
        st.markdown(shown)
    with st.chat_message("assistant"):
        with st.spinner("답변을 준비하는 중…"):
            try:
                if "question" in req:
                    ss.asked += 1
                    res = run_query(req["question"], ss.thread_id, None, ss.user_id)
                else:
                    res = run_query(None, ss.thread_id, req["resume"], ss.user_id, req["proposal_id"])
            except Exception as e:  # Bedrock 한도·자격 증명 오류 등
                res = {"answer": f"요청을 처리하지 못했습니다: {type(e).__name__}. 잠시 후 다시 시도해 주세요.",
                       "contexts": [], "trace": [], "route": ""}
    ss.history.append({"role": "assistant", "res": res})
    st.rerun()
