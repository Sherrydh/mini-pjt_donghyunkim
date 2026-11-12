"""장기 기억: 학습자 프로필 (LangGraph Store, SQLite 영구 저장).

- 프로필은 사용자별로 하나다. namespace=("profile", user_id), key="learner"
  {"level": "체르니 100번 진행 중", "current_pieces": [...], "completed_pieces": [...], "goal": "...", "updated_at": ...}
- planner 는 프로필을 읽어, 이미 아는 정보(수준·곡·목표)는 되묻지 않는다.
- 사용자 발화에서 프로필에 저장할 만한 정보를 추출하지만, 저장은 사용자가 승인했을 때만 한다 (HITL).
  잘못 알아듣고 저장하면(예: '체르니 30번 하고 싶어요' → 수준=체르니 30번) 이후 모든 추천이 틀어지기 때문이다.
"""
import sqlite3
from datetime import datetime, timezone

from langgraph.store.sqlite import SqliteStore
from pydantic import BaseModel, Field

from src.config import ROOT, llm

STORE_DB = ROOT / "memory.sqlite"
_conn = sqlite3.connect(str(STORE_DB), check_same_thread=False, isolation_level=None)
store = SqliteStore(_conn)
store.setup()

PROFILE_FIELDS = ("level", "current_pieces", "completed_pieces", "goal")


def _ns(user_id: str) -> tuple[str, str]:
    return ("profile", user_id)


def get_profile(user_id: str | None) -> dict:
    if not user_id:
        return {}
    item = store.get(_ns(user_id), "learner")
    return dict(item.value) if item else {}


def save_profile(user_id: str, profile: dict) -> dict:
    value = {k: profile.get(k) for k in PROFILE_FIELDS if profile.get(k)}
    value["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.put(_ns(user_id), "learner", value)
    return value


def delete_profile(user_id: str) -> None:
    store.delete(_ns(user_id), "learner")


def describe(profile: dict) -> str:
    """프로필을 프롬프트용 한 줄 요약으로 만든다."""
    if not profile:
        return "(저장된 프로필 없음)"
    parts = []
    if profile.get("level"):
        parts.append(f"수준: {profile['level']}")
    if profile.get("current_pieces"):
        parts.append(f"연습 중인 곡: {', '.join(profile['current_pieces'])}")
    if profile.get("completed_pieces"):
        parts.append(f"끝낸 곡: {', '.join(profile['completed_pieces'])}")
    if profile.get("goal"):
        parts.append(f"목표: {profile['goal']}")
    return " / ".join(parts) or "(저장된 프로필 없음)"


# ---------- 사용자 발화에서 프로필 정보 추출 ----------
class ProfileUpdate(BaseModel):
    level: str = Field(default="", description="사용자가 '지금' 배우고 있다고 직접 말한 단계 (예: '완전 처음', '체르니 100번 진행 중'). 없으면 빈 문자열")
    current_pieces: list[str] = Field(default_factory=list, description="지금 연습 중이라고 직접 말한 곡")
    completed_pieces: list[str] = Field(default_factory=list, description="끝냈다고 직접 말한 곡")
    goal: str = Field(default="", description="사용자가 직접 말한 목표·상황 (예: '취미, 하루 30분'). 없으면 빈 문자열")
    evidence: str = Field(default="", description="위 정보의 근거가 된 사용자 발화 인용 (짧게)")


EXTRACT_PROMPT = """사용자 발화에서 피아노 학습 프로필에 저장할 사실만 추출하세요.

규칙
- 사용자가 자기 상태를 **직접** 말한 것만 추출합니다. 바람·계획·질문은 제외합니다.
  · "체르니 100번 치고 있어요" → level='체르니 100번 진행 중'
  · "클레멘티 소나티네 Op.36 No.1 끝냈어" → completed_pieces=['클레멘티 소나티네 Op.36 No.1']
  · "체르니 30번 하고 싶어요", "소나타 칠 수 있을까?" → 아무것도 추출하지 않음 (바람·질문)
  · "모차르트 K.545 어려워?" → 아무것도 추출하지 않음 (곡에 대한 질문일 뿐 연습 중이라는 말이 없음)
- '직전 에이전트 질문'이 주어지면 사용자 발화를 그 질문에 대한 답으로 해석하세요.
  · 직전 질문 "어떤 곡인지 알려주시겠어요?" + 사용자 "모차르트 K.545요" → 아무것도 추출하지 않음
    (궁금한 곡을 알려준 것이지, 연습 중이라는 말이 아님)
  · 직전 질문 "어느 단계까지 배우셨나요?" + 사용자 "체르니 100번이요" → level='체르니 100번 진행 중'
- 추측하지 마세요. 해당 정보가 없으면 빈 값으로 두세요."""

_extractor = llm.with_structured_output(ProfileUpdate)


def extract_update(user_text: str, prev_agent_question: str = "") -> ProfileUpdate:
    msg = f"[직전 에이전트 질문] {prev_agent_question}\n[사용자 발화] {user_text}" if prev_agent_question else user_text
    return _extractor.invoke([("system", EXTRACT_PROMPT), ("human", msg)])


def merge(profile: dict, update: ProfileUpdate) -> dict:
    """기존 프로필에 추출 결과를 합친 '저장 제안'을 만든다. 바뀌는 것이 없으면 빈 dict."""
    proposed = {k: profile.get(k) for k in PROFILE_FIELDS if profile.get(k)}
    changed = False
    for key in ("level", "goal"):
        val = getattr(update, key).strip()
        if val and val != profile.get(key):
            proposed[key] = val
            changed = True
    for key in ("current_pieces", "completed_pieces"):
        old = list(profile.get(key) or [])
        new = [p for p in getattr(update, key) if p and p not in old]
        if new:
            proposed[key] = old + new
            changed = True
    # 끝낸 곡은 연습 중 목록에서 뺀다
    if proposed.get("completed_pieces") and proposed.get("current_pieces"):
        proposed["current_pieces"] = [p for p in proposed["current_pieces"] if p not in proposed["completed_pieces"]]
    return proposed if changed else {}
