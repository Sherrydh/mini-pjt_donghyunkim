"""Planner: 질문을 의도(intent)로 분해하고 의도별 필수 도구와 답변 체크리스트를 정한다 (Plan-Execute 패턴).

1차 평가에서 실패 6건 중 5건이 '필요한 도구를 부르지 않음'(A1)이었다. planner 가 계획을 세우면
agent 는 계획을 지시로 받고, check 노드는 계획된 도구가 실제로 호출됐는지 코드로 대조한다.
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
- 특정 곡의 난이도·테크닉을 말해야 하면 repertoire_lookup 을, 그 테크닉의 연습법까지 필요하면 technique 를 추가하세요.
- must_cover 에는 질문이 요구하는 답변 요소를 적으세요 (예: '시대별 대표 작곡가', '곡별 난이도 등급', '실제 존재하는 작품 번호').

[보조 검색어 coverage_queries — 쿼리 확장]
- 의도 검색어는 질문 전체를 요약한 것이라, must_cover 의 개별 사실 항목(대표 작곡가, 작곡 시기, 별칭 유래 등)을 놓치기 쉽습니다.
  이런 항목마다 그 항목을 직접 겨냥한 짧은 검색어를 만드세요.
  · must_cover '시대별 대표 작곡가'(바로크·고전주의 비교) → "바로크 대표 작곡가", "고전주의 대표 작곡가" (대상이 둘이면 각각)
  · must_cover '작곡 배경' (쇼팽 녹턴) → "쇼팽 녹턴 Op.9 No.2 작곡 시기 헌정"
  · must_cover '엄지 넘기기 연습법' → search_technique "엄지 넘기기 연습 방법"
- 검색어에는 항목의 핵심 단어(예: '작곡가', '유래', '연습법')를 반드시 넣으세요.
- 난이도·필요 테크닉 같은 곡 DB 항목, 의도 검색어와 사실상 같은 항목은 만들지 마세요. 필요 없으면 빈 리스트."""

_planner = llm.with_structured_output(Plan)


def make_plan(question: str) -> Plan:
    return _planner.invoke([("system", PLANNER_PROMPT), ("human", question)])
