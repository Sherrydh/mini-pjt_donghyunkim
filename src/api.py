"""FastAPI 서버: POST /query (과제 규약 응답 + route, refusal_reason)."""
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from src.agent import run_query

app = FastAPI(title="피아노 독학 길잡이 (Piano Study Companion)")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


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
    route: str = Field(description="answer | limited | refuse")
    refusal_reason: str = ""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> dict:
    return await run_in_threadpool(run_query, req.question)
