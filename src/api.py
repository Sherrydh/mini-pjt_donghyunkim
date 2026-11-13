"""FastAPI 서버: POST /query (과제 규약 응답 + 확장 필드), 학습 프로필 조회·삭제.

- 과제 규약 호출: {"question": "..."}
    → 단발형. 모호한 질문은 '되묻기 + 짧은 기본 경로'를 한 번에 답한다.
- 대화형: {"question": "...", "thread_id": "t1", "user_id": "u1"}
    → 대화 기록 유지. 수준·곡·목표가 빠져 답할 수 없으면 되묻는 턴(route="clarify")으로 답한다.
    → user_id 가 있으면 학습 프로필(장기 기억)을 읽어 맞춤 답을 하고,
      발화에 새 정보가 있으면 저장 승인을 요청한다 (status="need_input", pending_action).
- 승인 응답: {"thread_id": "t1", "resume": "승인"}  (또는 "거절")
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from src import memory
from src.agent import run_query

app = FastAPI(title="피아노 독학 길잡이 (Piano Study Companion)")


class QueryRequest(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=100, description="대화 ID (선택)")
    user_id: str | None = Field(default=None, min_length=1, max_length=100, description="학습 프로필 사용자 ID (선택)")
    resume: str | None = Field(default=None, min_length=1, max_length=200, description="저장 승인 응답: 승인/거절 (선택)")
    proposal_id: str | None = Field(
        default=None, min_length=1, max_length=64,
        description="승인하려는 저장 제안 ID (pending_action.proposal_id). 지금 대기 중인 제안과 다르면 저장하지 않는다 (선택)",
    )

    @model_validator(mode="after")
    def _check_combination(self):
        if self.resume is not None and not self.thread_id:
            raise ValueError("resume 은 thread_id 와 함께 보내야 합니다.")
        if self.question is None and self.resume is None:
            raise ValueError("question 또는 (thread_id + resume) 이 필요합니다.")
        return self


class Context(BaseModel):
    doc_id: str
    text: str
    source: str = ""


class TraceStep(BaseModel):
    step: str
    input: str
    output: str


class QueryResponse(BaseModel):
    answer: str
    contexts: list[Context]
    trace: list[TraceStep]
    route: str = Field(description="answer | limited | refuse | clarify | expired (만료된 저장 제안에 대한 승인 응답)")
    refusal_reason: str = ""
    status: str = Field(default="complete", description="complete | need_input (되묻기 또는 저장 승인 대기)")
    thread_id: str | None = None
    pending_action: dict | None = Field(default=None, description="승인 대기 중인 작업 (type=save_profile)")
    profile: dict | None = Field(default=None, description="현재 저장된 학습 프로필 (user_id 가 있을 때)")
    clarify_options: list[str] = Field(default_factory=list, description="되묻기 턴의 선택지 예시 (route=clarify 일 때)")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> dict:
    return await run_in_threadpool(run_query, req.question, req.thread_id, req.resume, req.user_id, req.proposal_id)


@app.get("/profile/{user_id}")
def get_profile(user_id: str) -> dict:
    return {"user_id": user_id, "profile": memory.get_profile(user_id)}


@app.delete("/profile/{user_id}")
def delete_profile(user_id: str) -> dict:
    memory.delete_profile(user_id)
    return {"user_id": user_id, "deleted": True}


# 채팅 화면 (src/static/index.html). API 경로를 먼저 등록한 뒤 마지막에 마운트해야 /query 등을 가리지 않는다.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
