"""Planner: 질문을 의도(intent)로 분해하고, 의도별 필수 도구와 답변 체크리스트를 정한다 (Plan-Execute 패턴).

1차 평가에서 실패 6건 중 5건이 '필요한 도구를 부르지 않음'(A1)이었다. planner 가 계획을 세우면
agent 는 계획을 지시로 받고, check 노드는 계획된 도구가 실제로 호출됐는지 코드로 대조한다.

추가 기능
- 쿼리 확장(coverage_queries): must_cover 항목을 직접 겨냥한 보조 검색어. prefetch 노드가 반드시 실행한다.
- 되묻기 판단(clarify, HITL): 사용자 수준·대상 곡·목표가 빠져 쓸모 있는 답을 할 수 없으면 되묻는다.
"""
from typing import Literal

from pydantic import BaseModel, Field

from src.config import llm

IntentType = Literal["technique", "knowledge", "repertoire_lookup", "recommendation"]

REQUIRED_TOOL = {
    "technique": "search_technique",
    "knowledge": "search_music_knowledge",
    "repertoire_lookup": "get_piece",
    "recommendation": "find_pieces",
}


class Intent(BaseModel):
    type: IntentType = Field(description="의도 유형")
    query: str = Field(description="이 의도를 위해 도구에 넣을 한국어 검색어 또는 곡 이름")


class CoverageQuery(BaseModel):
    item: str = Field(description="must_cover 중 이 검색이 겨냥하는 항목")
    tool: Literal["search_technique", "search_music_knowledge"] = Field(description="검색할 코퍼스")
    query: str = Field(description="그 항목을 직접 겨냥한 짧은 한국어 검색어 (항목의 핵심 단어를 반드시 포함)")


class Plan(BaseModel):
    intents: list[Intent] = Field(description="질문에 담긴 의도 목록 (1~4개)", min_length=1, max_length=4)
    must_cover: list[str] = Field(description="최종 답변에 반드시 들어가야 할 항목 (2~5개, 짧은 명사구)")
    coverage_queries: list[CoverageQuery] = Field(
        default_factory=list,
        description="must_cover 항목 중 의도 검색어만으로는 찾기 어려운 사실 항목을 겨냥한 보조 검색어 (0~4개)",
        max_length=4,
    )
    clarify: Literal["none", "ask", "follow_up"] = Field(
        default="none",
        description="되묻기 판단. ask=사용자 정보 없이는 쓸모 있는 답이 불가(멈추고 되묻기), "
        "follow_up=기본 답은 가능하나 정보가 있으면 더 맞출 수 있음(답 끝에 한 줄 질문), none=되묻기 불필요",
    )
    missing: Literal["", "level", "piece", "goal"] = Field(
        default="", description="빠진 정보 유형: level=사용자 수준, piece=대상 곡, goal=목표·상황. clarify=none 이면 빈 문자열"
    )
    clarify_question: str = Field(default="", description="사용자에게 되물을 한 문장 (존댓말). clarify=none 이면 빈 문자열")
    clarify_options: list[str] = Field(
        default_factory=list, description="선택지 예시 2~4개 (예: '완전 처음', '체르니 100번 진행 중')", max_length=4
    )

    @property
    def required_tools(self) -> list[str]:
        return sorted({REQUIRED_TOOL[i.type] for i in self.intents})


PLANNER_PROMPT = """당신은 피아노 학습 도우미의 계획 수립기입니다. 사용자 질문을 의도로 분해하세요.

[의도 유형]
- technique: 연습법·테크닉·연습 루틴·진도·입문 방법 (예: 하농, 스케일, 페달, 옥타브, 체르니 진도, '뭐부터 해야 돼')
- knowledge: 작곡가·작품 배경·별칭 유래·형식·음악사·시대 비교, 작품의 존재 여부 확인 (예: '쇼팽 협주곡 3번')
- repertoire_lookup: 특정 곡의 난이도·필요 테크닉 조회 (질문에 곡 이름이 있고 난이도/테크닉을 물을 때)
- recommendation: 다음 곡 추천, '이 곡/이 작곡가 곡 칠 수 있나', 수준에 맞는 곡 찾기

[규칙]
- 질문에 여러 요구가 섞여 있으면 의도를 모두 나열하세요. 예:
  · "K.545 난이도랑 미리 익힐 테크닉" → repertoire_lookup(K.545) + technique(알베르티 베이스·스케일 연습법)
  · "손이 작은데 쇼팽 곡 칠 수 있을까" → technique(작은 손 옥타브 요령) + recommendation(쇼팽 입문 곡)
  · "체르니 30번 몇 번까지 해야 소나타" → technique(연습곡 진도) + recommendation(소나타 입문 곡)
  · "OO 끝냈는데 다음 곡" → repertoire_lookup(OO) + recommendation
  · 사용자가 진도(바이엘·체르니 100번·30번 등)로 수준을 말하고 곡을 원하면 → technique("학습 단계와 Henle 난이도 대응 기준 체르니 OO번") + recommendation
- 특정 곡의 난이도·테크닉을 말해야 하면 repertoire_lookup 을, 그 테크닉의 연습법까지 필요하면 technique 를 추가하세요.
- must_cover 에는 질문이 요구하는 답변 요소를 적으세요 (예: '시대별 대표 작곡가', '곡별 난이도 등급', '실제 존재하는 작품 번호').

[보조 검색어 coverage_queries — 쿼리 확장]
- 의도 검색어는 질문 전체를 요약한 것이라, must_cover 의 개별 사실 항목(대표 작곡가, 작곡 시기, 별칭 유래 등)을 놓치기 쉽습니다.
  이런 항목마다 그 항목을 직접 겨냥한 짧은 검색어를 만드세요.
  · must_cover '시대별 대표 작곡가'(바로크·고전주의 비교) → "바로크 대표 작곡가", "고전주의 대표 작곡가" (대상이 둘이면 각각)
  · must_cover '작곡 배경' (쇼팽 녹턴) → "쇼팽 녹턴 Op.9 No.2 작곡 시기 헌정"
  · must_cover '엄지 넘기기 연습법' → search_technique "엄지 넘기기 연습 방법"
- 검색어에는 항목의 핵심 단어(예: '작곡가', '유래', '연습법')를 반드시 넣으세요.
- 난이도·필요 테크닉 같은 곡 DB 항목, 의도 검색어와 사실상 같은 항목은 만들지 마세요. 필요 없으면 빈 리스트.

[되묻기 판단 clarify — HITL]
아래 두 조건을 **모두** 만족할 때만 clarify="ask":
  (1) 사용자 수준(level)·대상 곡(piece)·목표(goal) 중 하나가 빠져 있고, 그에 따라 정답이 완전히 달라진다.
  (2) 누구에게나 통하는 쓸모 있는 기본 답이 없다.
기본 답은 가능하지만 정보가 있으면 더 맞출 수 있으면 clarify="follow_up". 일반 원칙으로 답할 수 있으면 clarify="none".
  · "뭐부터 해야 돼?" → ask, level ("지금 피아노를 어느 단계까지 배우셨나요?", 옵션: 완전 처음 / 바이엘·체르니 100번 진행 중 / 체르니 30번 이상)
  · "다음 곡 추천해줘"(기준 곡·수준 없음) → ask, level ("최근에 끝낸 곡이나 현재 단계를 알려주시겠어요?")
  · "이 곡 어려워?", "그 곡 페달 어떻게 해?"(곡이 특정되지 않음) → ask, piece ("어떤 곡인지 작곡가와 제목을 알려주시겠어요?")
  · "연습 얼마나 해야 돼?" → ask, goal ("목표(곡 완성/테크닉/취미)와 하루 연습 가능 시간을 알려주시겠어요?")
  · "손이 작은데 쇼팽 칠 수 있을까?" → follow_up, level (작은 손 요령+입문 곡으로 답하고 끝에 현재 수준을 물음)
  · "페달 언제 밟아?", "엄지 넘기기에서 소리가 끊겨요", "체르니 30번 몇 번까지 해야 소나타?", 특정 곡·작곡가·시대 질문 → none
clarify="ask" 여도 intents·must_cover 는 '기본 경로'로 채우세요 (되묻지 못하는 단발 호출에서는 되묻기 + 짧은 기본 답을 함께 냅니다)."""

_planner = llm.with_structured_output(Plan)


def _infer_missing(clarify_question: str) -> str:
    """LLM 이 missing 을 비워 둔 경우, 되묻는 문장에서 빠진 정보 유형을 추론한다."""
    if any(k in clarify_question for k in ("어떤 곡", "곡인지", "제목", "작곡가")):
        return "piece"
    if any(k in clarify_question for k in ("목표", "목적", "시간")):
        return "goal"
    return "level"


PROFILE_RULE = """[사용자 프로필 — 장기 기억]
{profile}
- 프로필로 이미 알 수 있는 정보(수준·연습 중인 곡·끝낸 곡·목표)는 되묻지 마세요. 그 정보로 답할 수 있으면 clarify="none" 입니다.
  · 프로필에 '끝낸 곡'이 있고 "다음 곡 추천해줘" → clarify=none, repertoire_lookup(끝낸 곡) + recommendation
  · 프로필에 수준이 있고 "뭐부터 해야 돼?" → clarify=none, 그 수준에 맞는 technique 계획
- 계획(검색어·must_cover)을 프로필 수준에 맞추세요."""


def make_plan(question: str, profile_desc: str = "") -> Plan:
    system = PLANNER_PROMPT
    if profile_desc:
        system += "\n\n" + PROFILE_RULE.format(profile=profile_desc)
    plan: Plan = _planner.invoke([("system", system), ("human", question)])
    if plan.clarify == "none":
        plan.missing, plan.clarify_question, plan.clarify_options = "", "", []
    elif not plan.missing:
        plan.missing = _infer_missing(plan.clarify_question)
    return plan
