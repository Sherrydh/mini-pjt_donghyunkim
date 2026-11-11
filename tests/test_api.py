"""POST /query 응답 계약 테스트 (실제 Bedrock 호출 — .env 자격 증명 필요).

    python -m pytest -q tests/test_api.py
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import app  # noqa: E402

client = TestClient(app)


def _check_contract(body: dict) -> None:
    assert set(["answer", "contexts", "trace", "route", "refusal_reason"]) <= body.keys()
    assert isinstance(body["answer"], str) and body["answer"]
    assert body["route"] in {"answer", "limited", "refuse"}
    for c in body["contexts"]:
        assert {"doc_id", "text", "source"} <= c.keys()
    for t in body["trace"]:
        assert {"step", "input", "output"} <= t.keys()
    assert body["trace"][0]["step"] == "guard"


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_answer_route_has_contexts_and_tool_trace():
    body = client.post("/query", json={"question": "모차르트 소나타 K.545 난이도 알려줘"}).json()
    _check_contract(body)
    assert body["route"] == "answer"
    assert body["contexts"], "근거 contexts 가 비어 있음"
    assert any(t["step"] == "get_piece" for t in body["trace"])


def test_other_instrument_refused_directly_from_guard():
    body = client.post("/query", json={"question": "바이올린 비브라토 하는 법 알려줘"}).json()
    _check_contract(body)
    assert body["route"] == "refuse"
    assert body["refusal_reason"]
    assert [t["step"] for t in body["trace"]] == ["guard", "refuse"]


@pytest.mark.parametrize("question", ["", "x" * 2001])
def test_invalid_question_rejected(question):
    assert client.post("/query", json={"question": question}).status_code == 422
