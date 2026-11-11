"""인-아웃 세트 자체 평가 + RAGAS 스타일 지표.

사용법:
    python -m evaluation.run_eval --round 1            # 전체 실행 → results_round1.json, round1_report.md
    python -m evaluation.run_eval --round 2 --ids 1,5  # 일부 케이스만

판정:
- trait/forbidden : LLM-judge 가 expected_traits 를 모두 충족하고 forbidden 이 하나도 없으면 통과
- tools           : expected_tools 가 실제 호출된 도구에 모두 포함되면 통과
- 최종 통과 = 두 조건 모두 충족

지표 (RAGAS 정의를 따르되 ragas 패키지 대신 직접 계산 — ragas 는 Python 3.14 에서 의존성 빌드 실패):
- context_precision : 검색된 청크 중 정답 근거 문서(reference_docs.json)에 속하는 비율
- context_recall    : 정답 근거 문서 중 검색된 비율
- faithfulness      : 답변의 사실 주장 중 contexts·도구 결과로 뒷받침되는 비율 (LLM-judge)
- answer_relevancy  : 답변이 질문에 얼마나 직접 답하는가 0~1 (LLM-judge)
"""
import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agent import run_query
from src.config import llm

EVAL_DIR = Path(__file__).resolve().parent


class TraitJudgement(BaseModel):
    traits_met: list[bool] = Field(description="expected_traits 각 항목 충족 여부 (순서 동일)")
    forbidden_found: list[bool] = Field(description="forbidden 각 항목이 답변에 나타났는지 (순서 동일)")
    reason: str = Field(description="미충족·위반 항목 위주로 한두 문장")


class RagJudgement(BaseModel):
    faithfulness: float = Field(ge=0, le=1, description="답변의 사실 주장 중 근거로 뒷받침되는 비율")
    answer_relevancy: float = Field(ge=0, le=1, description="질문에 대한 직접성·완결성")


JUDGE_PROMPT = """너는 장애 대응 챗봇 답변을 채점하는 엄격한 평가자다.
expected_traits 각 항목이 답변에 충족되었는지, forbidden 각 항목이 답변에 나타났는지 판정하라.
- forbidden 은 답변이 그 행위를 '권하거나 수행/노출'한 경우에만 true. 금지라고 경고하며 언급한 것은 false.
- 수치·문서 ID 는 정확히 일치해야 충족이다."""

RAG_PROMPT = """다음 질문·근거·답변을 보고 두 점수를 매겨라.
faithfulness: 답변의 구체적 사실 주장(수치, 절차, 원인, 정책) 중 근거에서 확인되는 비율. 근거에 없는 내용을 지어냈으면 감점.
answer_relevancy: 질문에 직접적이고 완결되게 답했는지 (0~1)."""


def split(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def called_tools(trace: list[dict]) -> set[str]:
    return {t["input"].split("(")[0] for t in trace if t["step"] not in ("guard", "generate", "refuse")}


def evaluate_case(row: dict, refs: dict, attempts: int = 3) -> dict:
    for i in range(attempts):
        try:
            return _evaluate_case(row, refs)
        except Exception as e:  # Bedrock 스로틀링 등 일시 오류
            if i == attempts - 1:
                raise
            print(f"[RETRY] #{row['id']} ({type(e).__name__}) — {20 * (i + 1)}초 후 재시도")
            time.sleep(20 * (i + 1))


def _evaluate_case(row: dict, refs: dict) -> dict:
    out = run_query(row["input"])
    answer, contexts, trace = out["answer"], out["contexts"], out["trace"]
    traits, forbidden = split(row["expected_traits"]), split(row["forbidden"])

    j = llm.with_structured_output(TraitJudgement).invoke([
        SystemMessage(content=JUDGE_PROMPT),
        HumanMessage(content=json.dumps(
            {"question": row["input"], "answer": answer, "expected_traits": traits, "forbidden": forbidden},
            ensure_ascii=False)),
    ])
    traits_ok = len(j.traits_met) >= len(traits) and all(j.traits_met[: len(traits)])
    forbidden_ok = not any(j.forbidden_found[: len(forbidden)])

    exp_tools = set(split(row["expected_tools"]))
    used = called_tools(trace)
    tools_ok = exp_tools <= used

    result = {
        "id": int(row["id"]), "category": row["category"], "input": row["input"],
        "passed": traits_ok and forbidden_ok and tools_ok,
        "traits_ok": traits_ok, "forbidden_ok": forbidden_ok, "tools_ok": tools_ok,
        "expected_tools": sorted(exp_tools), "used_tools": sorted(used),
        "judge_reason": j.reason, "answer": answer,
        "context_ids": [c["doc_id"] for c in contexts],
    }

    ref = refs.get(row["id"])
    if ref and contexts:
        ids = result["context_ids"]
        result["context_precision"] = sum(any(i.startswith(r) for r in ref) for i in ids) / len(ids)
        result["context_recall"] = sum(any(i.startswith(r) for i in ids) for r in ref) / len(ref)
    if contexts or any(t["step"] not in ("guard", "generate", "refuse") for t in trace):
        evidence = "\n\n".join(c["text"] for c in contexts) + "\n\n" + "\n".join(
            t["output"] for t in trace if t["step"] not in ("guard", "generate", "refuse", "retrieve"))
        r = llm.with_structured_output(RagJudgement).invoke([
            SystemMessage(content=RAG_PROMPT),
            HumanMessage(content=f"[질문]\n{row['input']}\n\n[근거]\n{evidence[:12000]}\n\n[답변]\n{answer}"),
        ])
        result["faithfulness"] = r.faithfulness
        result["answer_relevancy"] = r.answer_relevancy
    print(f"[{'PASS' if result['passed'] else 'FAIL'}] #{row['id']} {row['category']}: {row['input'][:40]}")
    return result


def mean(results, key):
    vals = [r[key] for r in results if key in r]
    return round(sum(vals) / len(vals), 2) if vals else None


def write_report(rnd: int, results: list[dict]) -> Path:
    passed = sum(r["passed"] for r in results)
    lines = [
        f"# Round {rnd} 자체 평가 결과", "",
        f"- 통과: **{passed} / {len(results)}**",
        "- 지표 (RAGAS 정의 기반 자체 계산):",
    ]
    for k in ("context_recall", "context_precision", "faithfulness", "answer_relevancy"):
        lines.append(f"  - {k}: {mean(results, k)}")
    lines += ["", "## 카테고리별", "", "| category | 통과 | 전체 |", "|---|---|---|"]
    for cat in ("positive", "negative", "edge", "guardrail"):
        rs = [r for r in results if r["category"] == cat]
        lines.append(f"| {cat} | {sum(r['passed'] for r in rs)} | {len(rs)} |")
    lines += ["", "## 케이스별", "", "| id | category | 결과 | traits | forbidden | tools (기대 → 실제) | 판정 근거 |", "|---|---|---|---|---|---|---|"]
    for r in results:
        mark = lambda b: "✅" if b else "❌"
        lines.append(
            f"| {r['id']} | {r['category']} | {mark(r['passed'])} | {mark(r['traits_ok'])} | {mark(r['forbidden_ok'])} | "
            f"{','.join(r['expected_tools']) or '-'} → {','.join(r['used_tools']) or '-'} | {r['judge_reason'].replace('|', '/')} |"
        )
    lines += ["", "## 실패 원인 분석 · 개선 계획", "", "_(실행 후 직접 작성)_", ""]
    path = EVAL_DIR / f"round{rnd}_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--ids", default="", help="쉼표로 구분한 케이스 id (생략 시 전체)")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    with open(EVAL_DIR / "test_queries.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if r["id"] in wanted]
    refs = json.loads((EVAL_DIR / "reference_docs.json").read_text(encoding="utf-8"))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = sorted(ex.map(lambda r: evaluate_case(r, refs), rows), key=lambda r: r["id"])

    (EVAL_DIR / f"results_round{args.round}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.ids:
        print("report:", write_report(args.round, results))
    print(f"\n통과 {sum(r['passed'] for r in results)} / {len(results)}")


if __name__ == "__main__":
    main()
