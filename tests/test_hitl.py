"""장기 기억(학습 프로필) + HITL(저장 승인) + 대화형 되묻기 시나리오 테스트.
실제 Bedrock 호출 — .env 자격 증명 필요.

    python -m pytest -q tests/test_hitl.py

검증 기준
- 대화형에서 수준·대상 곡·목표가 빠진 질문 → 되묻는 턴(route=clarify)으로 답하고, 다음 메시지를 대화 기록과 함께 처리한다
- 사용자 발화에 프로필 정보가 있으면 저장 승인을 요청하고(need_input + pending_action), 승인했을 때만 저장한다
- 저장된 프로필이 있으면 같은 질문도 되묻지 않고 프로필에 맞춰 답한다
- 단발형(thread_id·user_id 없음)은 멈추지 않는다
"""
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import app  # noqa: E402

client = TestClient(app)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _ask(**body) -> dict:
    r = client.post("/query", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def user():
    uid = _id("u")
    yield uid
    client.delete(f"/profile/{uid}")


def test_clarify_turn_then_answer_and_save_profile(user):
    """'뭐부터 해야 돼?' → 되묻기 턴 → 수준 답변 → 맞춤 답 + 저장 승인 → 승인 → 프로필 저장"""
    tid = _id("t")
    first = _ask(question="뭐부터 해야 돼?", thread_id=tid, user_id=user)
    assert first["route"] == "clarify" and first["status"] == "need_input", first
    assert first["pending_action"] is None  # 되묻기는 승인 대기가 아니라 일반 대화 턴
    assert any(t["step"] == "ask_user" for t in first["trace"])

    second = _ask(question="체르니 100번 치고 있어요", thread_id=tid, user_id=user)
    assert second["route"] == "answer"
    assert second["contexts"], "맞춤 답에 근거가 없음"
    assert second["status"] == "need_input"
    pending = second["pending_action"]
    assert pending and pending["type"] == "save_profile"
    assert "체르니" in pending["proposed"].get("level", "")
    assert client.get(f"/profile/{user}").json()["profile"] == {}  # 승인 전에는 저장되지 않음

    third = _ask(thread_id=tid, resume="승인")
    assert third["status"] == "complete"
    assert any(t["step"] == "approve_memory" and t["output"] == "saved" for t in third["trace"])
    assert "체르니" in client.get(f"/profile/{user}").json()["profile"].get("level", "")


def test_reject_does_not_save(user):
    tid = _id("t")
    body = _ask(question="체르니 100번 치고 있는데 다음에 뭐 칠까?", thread_id=tid, user_id=user)
    assert body["pending_action"], body
    rejected = _ask(thread_id=tid, resume="거절")
    assert any(t["step"] == "approve_memory" and t["output"] == "rejected" for t in rejected["trace"])
    assert client.get(f"/profile/{user}").json()["profile"] == {}


def test_saved_profile_avoids_reasking(user):
    """프로필에 끝낸 곡이 저장돼 있으면 '다음 곡 추천해줘'를 되묻지 않고 추천한다."""
    tid = _id("t")
    _ask(question="클레멘티 소나티네 Op.36 No.1 끝냈어요", thread_id=tid, user_id=user)
    _ask(thread_id=tid, resume="승인")
    assert client.get(f"/profile/{user}").json()["profile"].get("completed_pieces")

    body = _ask(question="다음 곡 추천해줘", thread_id=_id("t"), user_id=user)  # 새 대화
    assert body["route"] == "answer", body  # 되묻지 않음
    assert any(t["step"] == "find_pieces" for t in body["trace"])
    load = next(t for t in body["trace"] if t["step"] == "load_profile")
    assert "클레멘티" in load["output"]


def test_no_profile_info_no_approval(user):
    """프로필 정보가 없는 질문은 저장 승인을 묻지 않는다."""
    body = _ask(question="페달은 언제 밟고 언제 떼야 해?", thread_id=_id("t"), user_id=user)
    assert body["status"] == "complete" and body["pending_action"] is None, body


@pytest.mark.parametrize("question, missing", [("이 곡 어려워?", "piece"), ("연습 얼마나 해야 돼?", "goal")])
def test_conversational_clarify_types(question, missing):
    body = _ask(question=question, thread_id=_id("t"))
    assert body["route"] == "clarify", body
    step = next(t for t in body["trace"] if t["step"] == "ask_user")
    assert step["input"] == missing


def test_single_turn_never_pauses():
    body = _ask(question="뭐부터 해야 돼?")
    assert body["status"] == "complete" and body["route"] == "answer"
    assert "?" in body["answer"].splitlines()[0]  # 첫 줄이 되묻기


def test_resume_without_thread_id_rejected():
    assert client.post("/query", json={"resume": "승인"}).status_code == 422
