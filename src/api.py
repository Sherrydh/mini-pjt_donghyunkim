"""FastAPI 서버: POST /query"""
from fastapi import FastAPI
from pydantic import BaseModel

from src.agent import run_query

app = FastAPI(title="Incident Runbook Agent")


class QueryRequest(BaseModel):
    question: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query")
def query(req: QueryRequest):
    return run_query(req.question)
