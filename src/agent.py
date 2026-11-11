"""메인 에이전트 그래프 (LangGraph).

    START → guard ─┬─ refuse ──────────────────────────────────────────────────→ END
                   ├─ planner → (prefetch) → agent ⇄ tools ─→ check ─┬─ finalize → END
                   │                           ↑                     │
                   │                           └── 누락 도구 재요청 ──┘ (최대 1회)
                   └─ (limited) ──────────────→ agent ⇄ tools ─→ check → finalize → END

- guard    : 입력 가드레일 (규칙 + LLM 구조화 분류) → answer / limited / refuse
- planner  : 질문을 의도로 분해, 의도별 필수 도구·답변 체크리스트·항목별 보조 검색어 결정 (Plan-Execute)
- prefetch : planner 의 보조 검색어(쿼리 확장)를 에이전트보다 먼저 반드시 실행
- agent    : 도구 4종을 자율 선택·조합하는 ReAct 에이전트 (계획을 지시로 받음)
- check    : 계획된 필수 도구가 실제 호출됐는지 코드로 대조, 빠졌으면 agent 로 1회 되돌림
- finalize : LCEL 체인 + Pydantic 구조화 출력으로 최종 답변 정리, 실제 인용한 근거(doc_id)만 contexts 로 반환
run_query() 는 과제 규약 응답 {answer, contexts, trace} + route, refusal_reason 을 돌려준다.
"""
import json
import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from src.config import get_text, llm
from src.guard import LIMITED_INSTRUCTIONS, REFUSAL_MESSAGES, classify
from src.planner import make_plan
from src.tools import TOOLS, start_collection

MAX_TOOL_ROUNDS = 6
MAX_CHECK_RETRIES = 1


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    category: str
    route: str
    refusal_reason: str
    plan: dict
    answer: str
    used_doc_ids: list[str]
    tool_rounds: int
    check_retries: int
    trace: Annotated[list[dict], operator.add]


SYSTEM_PROMPT = """당신은 '피아노 독학 길잡이'입니다. 레슨 없이 혼자 클래식 피아노를 배우는 성인 입문자(체르니·소나티네 수준)를 돕습니다.

[도구]
- search_technique: 연습법·테크닉 문서 검색
- search_music_knowledge: 작곡가·작품·음악사 문서 검색
- get_piece: 특정 곡의 Henle 난이도·필요 테크닉 조회
- find_pieces: 난이도 범위·시대·테크닉 조건으로 곡 찾기 (추천)

[근거 규칙 — 반드시 지킬 것]
1. 답하기 전에 반드시 도구를 호출하세요. 사전 지식만으로 답하지 마세요.
2. 사실(연도, 헌정, 유래, 난이도 등급, 연습 원리)은 도구 결과에 있는 내용만 말하세요.
3. 난이도 등급은 get_piece/find_pieces 결과의 값만 'Henle 난이도 N' 형식으로 인용하세요.
4. 도구 결과에 없는 곡·작품·사실은 "제가 가진 자료에는 없습니다"라고 말하세요.
   질문의 전제가 자료와 다르면(예: 존재하지 않는 작품 번호) 도구 결과의 실제 사실로 바로잡으세요.
5. 곡을 추천할 때는 DB에 있는 곡만, 곡마다 난이도 등급과 추천 이유(필요 테크닉)를 적으세요.
6. 병명 진단·약 추천, 저작권 있는 악보·교재 원문 제공은 하지 않습니다.

[답변 형식]
- 첫 문장에서 질문에 바로 답하세요. 서비스 소개나 "도와드릴 수 있는 것" 목록은 쓰지 마세요.
- 이모지 금지. 정중한 존댓말. 핵심만 짧은 목록으로 (대략 15줄 이내).
- 질문과 무관한 부가 제안은 하지 마세요.
- 마지막 줄은 '출처: ' 뒤에 참고한 문서 제목 또는 URL을 나열하세요."""


def _plan_instruction(plan: dict) -> str:
    if not plan:
        return ""
    lines = ["[이번 질문의 계획 — 반드시 따를 것]"]
    for i, it in enumerate(plan["intents"], 1):
        lines.append(f"{i}. {it['type']}: '{it['query']}' → {it['tool']} 호출")
    lines.append(f"답변에 반드시 포함할 항목: {', '.join(plan['must_cover'])}")
    if plan.get("coverage_queries"):
        lines.append("포함할 항목을 겨냥한 보조 검색은 이미 실행됐습니다(위 도구 결과). 그 결과도 활용하세요.")
    lines.append("계획의 도구를 모두 호출한 뒤 답하세요. 같은 턴에 여러 도구를 동시에 호출해도 됩니다.")
    return "\n".join(lines)


# ---------- 노드 ----------
def guard(state: AgentState) -> dict:
    decision = classify(state["question"])
    route = decision.route
    return {
        "category": decision.category,
        "route": route,
        "refusal_reason": decision.reason if route != "answer" else "",
        "trace": [
            {
                "step": "guard",
                "input": state["question"],
                "output": f"category={decision.category}, route={route}, reason={decision.reason}",
            }
        ],
    }


def route_guard(state: AgentState) -> Literal["planner", "agent", "refuse"]:
    if state["route"] == "refuse":
        return "refuse"
    return "planner" if state["route"] == "answer" else "agent"


def refuse(state: AgentState) -> dict:
    answer = REFUSAL_MESSAGES[state["category"]]
    return {"answer": answer, "trace": [{"step": "refuse", "input": state["category"], "output": answer}]}


def planner(state: AgentState) -> dict:
    from src.planner import REQUIRED_TOOL

    plan = make_plan(state["question"])
    # 보조 검색으로 찾으러 간 항목은 답변 체크리스트에도 넣는다 (계획 안의 두 목록을 일치시킴).
    must_cover = list(plan.must_cover)
    for q in plan.coverage_queries:
        if q.item not in must_cover:
            must_cover.append(q.item)
    plan_dict = {
        "intents": [{"type": i.type, "query": i.query, "tool": REQUIRED_TOOL[i.type]} for i in plan.intents],
        "must_cover": must_cover,
        "coverage_queries": [q.model_dump() for q in plan.coverage_queries],
        "required_tools": plan.required_tools,
    }
    update: dict = {
        "plan": plan_dict,
        "trace": [{"step": "planner", "input": state["question"], "output": json.dumps(plan_dict, ensure_ascii=False)}],
    }
    # 쿼리 확장: must_cover 항목을 겨냥한 보조 검색어는 에이전트 판단에 맡기지 않고 prefetch 노드에서 반드시 실행한다.
    if plan.coverage_queries:
        update["messages"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": q.tool, "args": {"query": q.query}, "id": f"prefetch_{i}", "type": "tool_call"}
                    for i, q in enumerate(plan.coverage_queries)
                ],
            )
        ]
    return update


def route_planner(state: AgentState) -> Literal["prefetch", "agent"]:
    last = state["messages"][-1]
    return "prefetch" if isinstance(last, AIMessage) and last.tool_calls else "agent"


_agent_llm = llm.bind_tools(TOOLS)


def agent(state: AgentState) -> dict:
    system = SYSTEM_PROMPT
    if state.get("plan"):
        system += "\n\n" + _plan_instruction(state["plan"])
    if state["route"] == "limited":
        system += "\n\n" + LIMITED_INSTRUCTIONS[state["category"]]
    response = _agent_llm.invoke([SystemMessage(system), *state["messages"]])
    return {"messages": [response], "tool_rounds": state.get("tool_rounds", 0) + (1 if response.tool_calls else 0)}


def route_agent(state: AgentState) -> Literal["tools", "check"]:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls and state.get("tool_rounds", 0) <= MAX_TOOL_ROUNDS:
        return "tools"
    return "check"


def _called_tools(messages: list[BaseMessage]) -> set[str]:
    return {c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}


def check(state: AgentState) -> dict:
    """계획된 필수 도구가 실제로 호출됐는지 대조한다. 빠졌으면 에이전트에 재요청 메시지를 남긴다."""
    required = set((state.get("plan") or {}).get("required_tools", []))
    missing = sorted(required - _called_tools(state["messages"]))
    retry = bool(missing) and state.get("check_retries", 0) < MAX_CHECK_RETRIES
    update: dict = {
        "trace": [{"step": "check", "input": f"required={sorted(required)}", "output": f"missing={missing}, retry={retry}"}]
    }
    if retry:
        update["check_retries"] = state.get("check_retries", 0) + 1
        update["messages"] = [
            HumanMessage(
                f"[검수] 계획된 도구 {', '.join(missing)} 를 아직 호출하지 않았습니다. "
                "지금 호출하고, 그 결과까지 반영해 답변을 처음부터 다시 작성하세요."
            )
        ]
    return update


def route_check(state: AgentState) -> Literal["agent", "finalize"]:
    last = state["messages"][-1]
    return "agent" if isinstance(last, HumanMessage) and last.content.startswith("[검수]") else "finalize"


class FinalAnswer(BaseModel):
    """최종 답변 (Pydantic 구조화 출력)."""

    answer: str = Field(description="사용자에게 보여 줄 최종 답변 (존댓말, 마지막 줄에 '출처:' 포함)")
    used_doc_ids: list[str] = Field(description="최종 답변의 내용이 실제로 근거한 도구 결과의 doc_id 목록")
    grounded: bool = Field(description="답변의 사실이 모두 도구 결과에 근거하면 true")


_finalize_chain = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "당신은 답변 검수자입니다. 초안을 사용자에게 보낼 최종 답변으로 정리하세요.\n"
            "- 초안의 내용·구조·출처를 유지하세요. 도구 결과에 없는 사실(특히 난이도 등급·연도)은 삭제하세요.\n"
            "- '반드시 포함할 항목' 중 초안에 빠졌지만 도구 결과에 근거가 있는 것은 짧게 보충하세요. 근거가 없으면 보충하지 마세요.\n"
            "- 이모지와 서비스 소개 문구, 질문과 무관한 부가 제안은 지우세요.\n"
            "- '출처:' 줄이 없으면 도구 결과의 문서 제목/URL로 추가하세요.\n"
            "- used_doc_ids 에는 최종 답변이 실제로 사용한 도구 결과의 doc_id 만 넣으세요 ([doc_id: ...] 표기 참고).\n"
            "- 초안이 비어 있으면 도구 결과만으로 짧게 답하세요.",
        ),
        (
            "human",
            "질문:\n{question}\n\n반드시 포함할 항목: {must_cover}\n\n도구 결과:\n{evidence}\n\n초안:\n{draft}",
        ),
    ]
) | llm.with_structured_output(FinalAnswer)


def finalize(state: AgentState) -> dict:
    last = state["messages"][-1]
    draft = get_text(last) if isinstance(last, AIMessage) else ""
    evidence = "\n\n".join(m.content for m in state["messages"] if isinstance(m, ToolMessage)) or "(도구 호출 없음)"
    must_cover = ", ".join((state.get("plan") or {}).get("must_cover", [])) or "(없음)"
    result: FinalAnswer = _finalize_chain.invoke(
        {"question": state["question"], "must_cover": must_cover, "evidence": evidence[:14000], "draft": draft}
    )
    return {
        "answer": result.answer,
        "used_doc_ids": result.used_doc_ids,
        "trace": [
            {
                "step": "finalize",
                "input": _short(draft),
                "output": f"grounded={result.grounded}, used_doc_ids={result.used_doc_ids}",
            }
        ],
    }


# ---------- 그래프 ----------
builder = StateGraph(AgentState)
builder.add_node("guard", guard)
builder.add_node("refuse", refuse)
builder.add_node("planner", planner)
builder.add_node("prefetch", ToolNode(TOOLS))
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_node("check", check)
builder.add_node("finalize", finalize)

builder.add_edge(START, "guard")
builder.add_conditional_edges("guard", route_guard)
builder.add_conditional_edges("planner", route_planner)
builder.add_edge("prefetch", "agent")
builder.add_conditional_edges("agent", route_agent)
builder.add_edge("tools", "agent")
builder.add_conditional_edges("check", route_check)
builder.add_edge("refuse", END)
builder.add_edge("finalize", END)
graph = builder.compile()


def _short(text: str, n: int = 500) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n] + "…"


def _tool_trace(messages: list[BaseMessage]) -> list[dict]:
    """메시지 이력에서 도구 호출 단계를 trace 항목으로 뽑는다."""
    outputs = {m.tool_call_id: m.content for m in messages if isinstance(m, ToolMessage)}
    steps = []
    for m in messages:
        if isinstance(m, AIMessage):
            for call in m.tool_calls:
                steps.append(
                    {
                        "step": call["name"],
                        "input": ("[prefetch] " if call["id"].startswith("prefetch_") else "")
                        + json.dumps(call["args"], ensure_ascii=False),
                        "output": _short(outputs.get(call["id"], "")),
                    }
                )
    return steps


def _select_contexts(collected: list[dict], used_ids: list[str]) -> list[dict]:
    """finalize 가 실제로 인용한 근거만 남긴다. 인용 정보가 없거나 하나도 맞지 않으면 전부 반환한다."""
    used = set(used_ids or [])
    picked = [c for c in collected if c["doc_id"] in used]
    return picked or collected


def run_query(question: str) -> dict:
    collected = start_collection()
    state = graph.invoke(
        {
            "messages": [HumanMessage(question)],
            "question": question,
            "trace": [],
            "plan": {},
            "used_doc_ids": [],
            "tool_rounds": 0,
            "check_retries": 0,
        },
        config={"recursion_limit": 40, "run_name": "piano-study-agent"},
    )
    head = [t for t in state["trace"] if t["step"] in ("guard", "refuse", "planner")]
    tail = [t for t in state["trace"] if t["step"] in ("check", "finalize")]
    contexts = _select_contexts(collected, state.get("used_doc_ids", []))
    return {
        "answer": state.get("answer", ""),
        "contexts": [{"doc_id": c["doc_id"], "text": c["text"], "source": c.get("source", "")} for c in contexts],
        "trace": head + _tool_trace(state["messages"]) + tail,
        "route": state["route"],
        "refusal_reason": state.get("refusal_reason", ""),
    }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "하농 연습을 효과적으로 하려면 어떻게 해야 해?"
    print(json.dumps(run_query(q), ensure_ascii=False, indent=2))
