"""메인 에이전트 그래프 (LangGraph).

    START → guard ─┬─ refuse ─────────────────────────────────────────────────────────→ END
                   └─ load_profile ─┬─ planner ─┬─ ask_user (대화형 모드에서 되묻기 턴) ──────→ END
                                    │           └─ (prefetch) → agent ⇄ tools → check → finalize ─┐
                                    └─ (limited) ──────────→ agent ⇄ tools → check → finalize ────┤
                                                                                                  ↓
                          (user_id 가 있으면) propose_memory → approve_memory ⏸ interrupt (승인) → END

- guard          : 입력 가드레일 (규칙 + LLM 구조화 분류) → answer / limited / refuse
- load_profile   : 장기 기억(LangGraph Store)에서 학습자 프로필(수준·연습 중인 곡·끝낸 곡·목표)을 읽는다
- planner        : 질문을 의도로 분해, 의도별 필수 도구·답변 체크리스트·보조 검색어, 되묻기 판단 (Plan-Execute)
                   프로필로 알 수 있는 정보는 되묻지 않는다
- ask_user       : (대화형) 수준·대상 곡·목표가 빠져 쓸모 있는 답이 불가하면 되묻는 턴으로 끝낸다.
                   사용자의 다음 메시지는 같은 thread 의 대화 기록과 함께 처리된다 (체크포인터)
- prefetch       : planner 의 보조 검색어(쿼리 확장)를 에이전트보다 먼저 반드시 실행
- agent          : 도구 4종을 자율 선택·조합하는 ReAct 에이전트 (계획·프로필을 지시로 받음)
- check          : 계획된 필수 도구가 실제 호출됐는지 코드로 대조, 빠졌으면 agent 로 1회 되돌림
- finalize       : 리랭킹 + LCEL 체인·Pydantic 구조화 출력으로 최종 답변 정리
- propose_memory : 이번 사용자 발화에서 프로필에 저장할 정보를 추출해 저장 제안을 만든다
- approve_memory : (HITL) interrupt 로 멈추고 사용자 승인을 받은 경우에만 프로필에 저장한다
run_query() 는 과제 규약 응답 {answer, contexts, trace} + route, refusal_reason, status, thread_id, pending_action, profile 을 돌려준다.
"""
import json
import operator
import sqlite3
import uuid
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from src import memory
from src.config import ROOT, get_text, llm
from src.guard import LIMITED_INSTRUCTIONS, REFUSAL_MESSAGES, classify
from src.planner import REQUIRED_TOOL, make_plan
from src.retriever import rerank
from src.tools import TOOLS, collected, start_collection

MAX_TOOL_ROUNDS = 6
MAX_CHECK_RETRIES = 1
APPROVE_WORDS = ("승인", "예", "네", "응", "저장", "좋아", "yes", "y", "ok")


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    category: str
    route: str
    refusal_reason: str
    plan: dict
    answer: str
    used_doc_ids: list[str]
    ranked_doc_ids: list[str]
    tool_rounds: int
    check_retries: int
    conversational: bool  # thread_id 로 호출된 대화형 모드 (대화 기록 유지, 되묻기 턴 가능)
    user_id: str  # 장기 기억 사용자 (없으면 "")
    profile: dict  # 이번 턴 시작 시 프로필
    proposed_profile: dict  # 저장 제안 (없으면 {})
    proposal_id: str  # 저장 제안 ID — 오래된 승인 카드가 새 제안을 승인하지 않도록 구분한다
    memory_result: str  # "saved" | "rejected" | ""
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
5. 곡을 추천할 때는 DB에 있는 곡만, 곡마다 '곡명 (Henle 난이도 N)' 형식으로 등급을 붙이고 추천 이유(필요 테크닉)를 적으세요.
   사용자가 자기 수준·최근 곡을 **전혀 말하지 않았고** 프로필에도 없을 때만, DB에서 가장 쉬운 등급의 곡부터 고르고
   "수준을 모르므로 입문 등급부터 제안합니다"라고 밝히세요.
   사용자가 진도로 수준을 말했다면(예: '체르니 100번', '바이엘') 모른다고 하지 말고,
   search_technique 로 '학습 단계와 Henle 난이도 대응 기준' 문서를 찾아 그 기준으로 find_pieces 의 등급 범위를 정하세요.
   이 대응은 공식 대응표가 아닌 참고 기준이므로 답변에 "참고 기준"임을 밝히고, 문서에 없는 대응 관계는 지어내지 마세요.
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



def route_guard(state: AgentState) -> Literal["load_profile", "refuse"]:
    return "refuse" if state["route"] == "refuse" else "load_profile"


def refuse(state: AgentState) -> dict:
    answer = REFUSAL_MESSAGES[state["category"]]
    return {"answer": answer, "trace": [{"step": "refuse", "input": state["category"], "output": answer}]}



def load_profile(state: AgentState) -> dict:
    profile = memory.get_profile(state.get("user_id"))
    return {
        "profile": profile,
        "trace": [{"step": "load_profile", "input": state.get("user_id") or "(익명)", "output": memory.describe(profile)}],
    }


def route_profile(state: AgentState) -> Literal["planner", "agent"]:
    return "planner" if state["route"] == "answer" else "agent"


def _conversation_question(state: AgentState) -> str:
    """대화형 모드면 이전 사용자 발화와 에이전트의 마지막 되물음을 현재 질문 앞에 붙인다."""
    msgs = state["messages"]
    # 이번 턴의 사용자 메시지(마지막으로 등장한 현재 질문) 이전까지만 '이전 대화'로 본다
    cur = max((i for i, m in enumerate(msgs) if isinstance(m, HumanMessage) and m.content == state["question"]), default=len(msgs))
    prev = msgs[:cur]
    if not state.get("conversational") or not prev:
        return state["question"]
    humans = [m.content for m in prev if isinstance(m, HumanMessage) and not str(m.content).startswith("[검수]")]
    last_ai = next((get_text(m) for m in reversed(prev) if isinstance(m, AIMessage) and get_text(m)), "")
    lines = ["[이전 대화]"] + [f"- 사용자: {h}" for h in humans[-3:]]
    if last_ai:
        lines.append(f"- 에이전트(마지막 답): {last_ai[:200]}")
    lines.append(f"[현재 메시지] {state['question']}")
    return "\n".join(lines)


def planner(state: AgentState) -> dict:
    question = _conversation_question(state)
    profile_desc = memory.describe(state.get("profile") or {}) if state.get("user_id") else ""
    plan = make_plan(question, profile_desc)
    # 곡 추천이면 '학습 단계 ↔ Henle 난이도 대응 기준' 문서를 반드시 근거로 확보한다.
    # (프롬프트 규칙만으로는 planner 가 이 검색을 다른 인덱스로 보내거나 빠뜨렸다 — round2_report 참고)
    if any(i.type == "recommendation" for i in plan.intents):
        from src.planner import CoverageQuery

        plan.coverage_queries.append(
            CoverageQuery(
                item="사용자 수준에 맞는 Henle 등급 범위(대응 기준 문서 근거)",
                tool="search_technique",
                query=f"학습 단계와 Henle 난이도 대응 기준 {state['question']}",
            )
        )
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
        "clarify": plan.clarify,
        "missing": plan.missing,
        "clarify_question": plan.clarify_question,
        "clarify_options": plan.clarify_options,
    }
    update: dict = {
        "plan": plan_dict,
        "trace": [{"step": "planner", "input": question, "output": json.dumps(plan_dict, ensure_ascii=False)}],
    }
    if _asks_user(state, plan_dict):  # 대화형: 검색하지 않고 되묻는 턴으로 끝낸다
        return update
    # 쿼리 확장: must_cover 항목을 겨냥한 보조 검색어는 에이전트 판단에 맡기지 않고 prefetch 노드에서 반드시 실행한다.
    if plan.coverage_queries:
        update["messages"] = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": q.tool, "args": {"query": q.query}, "id": f"prefetch_{uuid.uuid4().hex[:8]}", "type": "tool_call"}
                    for q in plan.coverage_queries
                ],
            )
        ]
    return update


def _asks_user(state: AgentState, plan: dict) -> bool:
    return bool(state.get("conversational")) and plan.get("clarify") == "ask"


def route_planner(state: AgentState) -> Literal["ask_user", "prefetch", "agent"]:
    if _asks_user(state, state.get("plan") or {}):
        return "ask_user"
    last = state["messages"][-1]
    return "prefetch" if isinstance(last, AIMessage) and last.tool_calls else "agent"


def _clarify_text(plan: dict) -> str:
    opts = plan.get("clarify_options") or []
    return plan.get("clarify_question", "") + (f" (예: {' / '.join(opts)})" if opts else "")


def ask_user(state: AgentState) -> dict:
    """대화형 모드의 되묻기 턴: 빠진 정보를 묻고 이번 턴을 끝낸다. 다음 메시지는 대화 기록과 함께 처리된다."""
    text = _clarify_text(state["plan"])
    return {
        "answer": text,
        "route": "clarify",
        "messages": [AIMessage(text)],
        "trace": [{"step": "ask_user", "input": state["plan"].get("missing", ""), "output": text}],
    }


_agent_llm = llm.bind_tools(TOOLS)


def agent(state: AgentState) -> dict:
    system = SYSTEM_PROMPT
    if state.get("plan"):
        system += "\n\n" + _plan_instruction(state["plan"])
    if state.get("user_id") and state.get("profile"):
        system += (
            "\n\n[사용자 프로필 — 장기 기억] " + memory.describe(state["profile"])
            + "\n추천·설명은 이 수준에 맞추세요. 끝낸 곡은 다시 추천하지 마세요."
        )
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
            "- 초안이 비어 있으면 도구 결과만으로 짧게 답하세요.\n"
            "- 곡을 추천했다면 곡마다 'Henle 난이도 N'이 붙어 있는지 확인하고, 빠졌으면 도구 결과의 값으로 채우세요. "
            "도구 결과에 등급이 없는 곡은 추천에서 빼세요.\n"
            "- '되묻기 지시'가 있으면 그대로 따르세요.",
        ),
        (
            "human",
            "질문:\n{question}\n\n반드시 포함할 항목: {must_cover}\n"
            "되묻기 지시: {clarify_note}\n\n"
            "도구 결과:\n{evidence}\n\n초안:\n{draft}",
        ),
    ]
) | llm.with_structured_output(FinalAnswer)



def _clarify_note(state: AgentState, plan: dict) -> str:
    """단발 호출(되묻고 기다릴 수 없음)이거나 follow_up 이면, 되묻기 문장을 답변에 넣도록 지시한다."""
    if not plan.get("clarify_question"):
        return "(없음)"
    if plan.get("clarify") == "ask":
        return (
            "이 질문은 사용자 정보가 없어 맞춤 답을 할 수 없습니다. 답변 전체를 다음 세 부분으로만 구성하세요 "
            "(소제목·추가 섹션·주의사항 금지): "
            f"① 첫 줄: '{_clarify_text(plan)}' "
            "② '가장 흔한 경우(완전 처음)라면' 으로 시작하는 기본 경로 3~4줄 (도구 결과 근거) "
            "③ 출처 줄. '반드시 포함할 항목'은 이 3~4줄 안에서만 다루세요."
        )
    if plan.get("clarify") == "follow_up":
        return f"답변 본문 끝(출처 줄 바로 앞)에 한 줄로 덧붙이세요: '{plan['clarify_question']}'"
    return "(없음)"


def finalize(state: AgentState) -> dict:
    last = state["messages"][-1]
    draft = get_text(last) if isinstance(last, AIMessage) else ""
    # 리랭킹: 도구 호출 순서가 아니라 질문과의 관련도 순으로 근거를 정렬해 검수 단계에 넘긴다.
    question = _conversation_question(state)
    ranked = rerank(question, collected())
    if ranked:
        evidence = "\n\n".join(
            f"[doc_id: {c['doc_id']}] (출처: {c.get('source') or '자체 작성'})\n{c['text']}" for c in ranked
        )
    else:
        evidence = "\n\n".join(m.content for m in state["messages"] if isinstance(m, ToolMessage)) or "(도구 호출 없음)"
    plan = state.get("plan") or {}
    result: FinalAnswer = _finalize_chain.invoke(
        {
            "question": question,
            "must_cover": ", ".join(plan.get("must_cover", [])) or "(없음)",
            "clarify_note": _clarify_note(state, plan),
            "evidence": evidence[:14000],
            "draft": draft,
        }
    )
    return {
        "answer": result.answer,
        "used_doc_ids": result.used_doc_ids,
        "ranked_doc_ids": [c["doc_id"] for c in ranked],
        "trace": [
            {
                "step": "rerank",
                "input": f"{len(ranked)} contexts",
                "output": ", ".join(f"{c['doc_id']}={c['rerank_score']}" for c in ranked[:8]),
            },
            {
                "step": "finalize",
                "input": _short(draft),
                "output": f"grounded={result.grounded}, used_doc_ids={result.used_doc_ids}",
            }
        ],
    }



def route_finalize(state: AgentState) -> Literal["propose_memory", "__end__"]:
    return "propose_memory" if state.get("user_id") and state.get("conversational") else END


def _previous_agent_question(state: AgentState) -> str:
    """이번 턴 사용자 메시지 직전의 에이전트 발화가 되묻기였으면 그 문장을 돌려준다 (프로필 추출 맥락용)."""
    msgs = state["messages"]
    cur = max(
        (i for i, m in enumerate(msgs) if isinstance(m, HumanMessage) and m.content == state["question"]),
        default=None,
    )
    if not cur:
        return ""
    prev = msgs[cur - 1]
    text = get_text(prev) if isinstance(prev, AIMessage) else ""
    return text if "?" in text else ""


def propose_memory(state: AgentState) -> dict:
    """이번 사용자 발화에서 프로필 정보를 추출해 저장 제안을 만든다 (저장은 하지 않는다)."""
    update = memory.extract_update(state["question"], _previous_agent_question(state))
    proposed = memory.merge(state.get("profile") or {}, update)
    return {
        "proposed_profile": proposed,
        "proposal_id": uuid.uuid4().hex[:12] if proposed else "",
        "trace": [
            {
                "step": "propose_memory",
                "input": state["question"],
                "output": json.dumps({"proposed": proposed, "evidence": update.evidence}, ensure_ascii=False),
            }
        ],
    }


def route_memory(state: AgentState) -> Literal["approve_memory", "__end__"]:
    return "approve_memory" if state.get("proposed_profile") else END


def approve_memory(state: AgentState) -> dict:
    """HITL: 프로필 저장 전에 멈추고 사용자 승인을 받는다. 승인한 경우에만 Store 에 쓴다.
    (재개 시 이 노드는 처음부터 다시 실행되므로, LLM 호출 없이 interrupt 만 둔다)"""
    proposed = state["proposed_profile"]
    decision = interrupt(
        {
            "type": "save_profile",
            "proposal_id": state.get("proposal_id", ""),
            "message": "이 내용을 내 학습 프로필에 저장할까요? 저장하면 다음부터 이 정보에 맞춰 답합니다. (승인/거절)",
            "current": state.get("profile") or {},
            "proposed": proposed,
        }
    )
    approved = str(decision).strip().lower() in APPROVE_WORDS
    if approved:
        saved = memory.save_profile(state["user_id"], proposed)
        answer = f"학습 프로필을 저장했습니다. ({memory.describe(saved)}) 다음부터 이 정보에 맞춰 답해 드릴게요."
    else:
        answer = "프로필은 저장하지 않았습니다. 필요하면 언제든 수준이나 연습 중인 곡을 다시 알려주세요."
    return {
        "answer": answer,
        "memory_result": "saved" if approved else "rejected",
        "trace": [{"step": "approve_memory", "input": str(decision), "output": "saved" if approved else "rejected"}],
    }


# ---------- 그래프 ----------
builder = StateGraph(AgentState)
builder.add_node("guard", guard)
builder.add_node("refuse", refuse)
builder.add_node("load_profile", load_profile)
builder.add_node("planner", planner)
builder.add_node("ask_user", ask_user)
builder.add_node("prefetch", ToolNode(TOOLS))
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_node("check", check)
builder.add_node("finalize", finalize)
builder.add_node("propose_memory", propose_memory)
builder.add_node("approve_memory", approve_memory)

builder.add_edge(START, "guard")
builder.add_conditional_edges("guard", route_guard)
builder.add_conditional_edges("load_profile", route_profile)
builder.add_conditional_edges("planner", route_planner)
builder.add_edge("prefetch", "agent")
builder.add_conditional_edges("agent", route_agent)
builder.add_edge("tools", "agent")
builder.add_conditional_edges("check", route_check)
builder.add_conditional_edges("finalize", route_finalize)
builder.add_conditional_edges("propose_memory", route_memory)
builder.add_edge("ask_user", END)
builder.add_edge("refuse", END)
builder.add_edge("approve_memory", END)
graph = builder.compile()  # 단발형(과제 규약): 체크포인터 없음, 되묻기는 답변 안에 포함

# 대화형: 대화 기록과 승인 대기(interrupt) 상태를 SQLite 체크포인터에 저장한다.
CHECKPOINT_DB = ROOT / "checkpoints.sqlite"
_checkpointer = SqliteSaver(sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False))
chat_graph = builder.compile(checkpointer=_checkpointer)


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


def _select_contexts(bucket: list[dict], used_ids: list[str], ranked_ids: list[str]) -> list[dict]:
    """finalize 가 실제로 인용한 근거만 남기고, 리랭킹 순서(질문 관련도 순)로 정렬한다.
    인용 정보가 없거나 하나도 맞지 않으면 전부 반환한다."""
    used = set(used_ids or [])
    picked = [c for c in bucket if c["doc_id"] in used] or list(bucket)
    rank = {doc_id: i for i, doc_id in enumerate(ranked_ids or [])}
    return sorted(picked, key=lambda c: rank.get(c["doc_id"], len(rank)))



def _turn_input(question: str, conversational: bool, user_id: str) -> dict:
    """한 턴의 입력. 대화형에서는 messages 만 누적되고 나머지 턴 단위 필드는 초기화된다."""
    return {
        "messages": [HumanMessage(question)],
        "question": question,
        "category": "",
        "route": "",
        "refusal_reason": "",
        "plan": {},
        "answer": "",
        "used_doc_ids": [],
        "ranked_doc_ids": [],
        "tool_rounds": 0,
        "check_retries": 0,
        "conversational": conversational,
        "user_id": user_id,
        "profile": {},
        "proposed_profile": {},
        "proposal_id": "",
        "memory_result": "",
    }


def _pending_proposal(g, config: dict) -> dict | None:
    """이 대화에서 승인을 기다리는 저장 제안 (없으면 None)."""
    snapshot = g.get_state(config)
    for task in snapshot.tasks or ():
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


def _expired_resume(thread_id: str, user_id: str | None, resume: str, pending: dict | None) -> dict:
    """대기 중인 제안이 없거나 다른 제안일 때의 승인 응답: 아무것도 저장하지 않는다."""
    if pending:
        answer = ("이 저장 제안은 만료됐습니다. 그 사이 새 질문을 보내 새 제안이 만들어졌습니다. "
                  "아무것도 저장하지 않았으니 가장 최근 저장 확인에서 선택해 주세요.")
    else:
        answer = ("이 저장 제안은 만료됐습니다. 승인하기 전에 새 질문을 보내 제안이 취소됐고, 아무것도 저장하지 않았습니다. "
                  "저장하려면 수준이나 연습 중인 곡을 다시 알려 주세요.")
    return {
        "answer": answer,
        "contexts": [],
        "trace": [{"step": "approve_memory", "input": resume, "output": "expired"}],
        "route": "expired",
        "refusal_reason": "",
        "status": "complete",
        "thread_id": thread_id,
        "pending_action": None,
        "profile": memory.get_profile(user_id) if user_id else None,
        "clarify_options": [],
    }


def run_query(
    question: str | None = None,
    thread_id: str | None = None,
    resume: str | None = None,
    user_id: str | None = None,
    proposal_id: str | None = None,
) -> dict:
    """질문을 처리한다.

    - thread_id·user_id 없음: 단발형 (과제 규약). 모호한 질문은 '되묻기 + 짧은 기본 경로'를 한 번에 답한다.
    - thread_id (+ user_id): 대화형. 대화 기록을 유지하고, 모호하면 되묻는 턴(route="clarify")으로 답한다.
      user_id 가 있으면 프로필을 읽어 쓰고, 새 정보가 있으면 저장 승인을 요청한다(status="need_input").
    - thread_id + resume: 승인 대기 중인 저장 제안에 답한다 ("승인" / "거절").
      대기 중인 제안이 없거나(그 사이 새 질문을 보내 취소됨) proposal_id 가 지금 제안과 다르면
      아무것도 저장하지 않고 route="expired" 로 답한다.
    """
    bucket = start_collection()
    config: dict = {"recursion_limit": 40, "run_name": "piano-study-agent"}
    if user_id and not thread_id:
        thread_id = f"{user_id}-{uuid.uuid4().hex[:8]}"
    if thread_id:
        g = chat_graph
        config["configurable"] = {"thread_id": thread_id}
        if resume is not None:
            pending = _pending_proposal(g, config)
            if pending is None or (proposal_id and pending.get("proposal_id") != proposal_id):
                return _expired_resume(thread_id, user_id, resume, pending)
        prev = g.get_state(config).values or {}
        trace_start, msg_start = len(prev.get("trace", [])), len(prev.get("messages", []))
        payload = Command(resume=resume) if resume is not None else _turn_input(question, True, user_id or "")
    else:
        g = graph
        trace_start = msg_start = 0
        payload = _turn_input(question, False, "")
    state = g.invoke(payload, config=config)

    turn_trace = state["trace"][trace_start:]
    head = [t for t in turn_trace if t["step"] in ("guard", "refuse", "load_profile", "planner", "ask_user")]
    tail = [t for t in turn_trace if t["step"] in ("check", "rerank", "finalize", "propose_memory", "approve_memory")]
    contexts = _select_contexts(bucket, state.get("used_doc_ids", []), state.get("ranked_doc_ids", []))
    result = {
        "answer": state.get("answer", ""),
        "contexts": [{"doc_id": c["doc_id"], "text": c["text"], "source": c.get("source", "")} for c in contexts],
        "trace": head + _tool_trace(state["messages"][msg_start:]) + tail,
        "route": state.get("route", ""),
        "refusal_reason": state.get("refusal_reason", ""),
        "status": "need_input" if state.get("route") == "clarify" else "complete",
        "thread_id": thread_id,
        "pending_action": None,
        "profile": memory.get_profile(user_id) if user_id else None,
        "clarify_options": (state.get("plan") or {}).get("clarify_options", []) if state.get("route") == "clarify" else [],
    }
    if resume is not None:  # 승인 응답 턴: 검색 근거 없이 저장 결과만 돌려준다
        result["contexts"] = []
    if state.get("__interrupt__"):  # HITL: 프로필 저장 승인 대기
        pending = state["__interrupt__"][0].value
        result["status"] = "need_input"
        result["pending_action"] = pending
        result["answer"] = state.get("answer", "") + "\n\n[학습 프로필 저장 확인] " + pending["message"] + (
            f" 제안: {memory.describe(pending['proposed'])}"
        )
    return result


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "하농 연습을 효과적으로 하려면 어떻게 해야 해?"
    print(json.dumps(run_query(q), ensure_ascii=False, indent=2))
