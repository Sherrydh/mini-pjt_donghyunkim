"""인-아웃 세트 자체 평가: LLM-as-judge 통과 판정 + RAGAS 4지표.

사용법:
    python -m evaluation.run_eval --round 1
    python -m evaluation.run_eval --round 2 --ids 7,8,12
    python -m evaluation.run_eval --round 1 --skip-ragas

통과 기준 (SERVICE.md §5):
    expected_traits 전부 충족 AND forbidden 항목 0건 AND expected_tools 가 trace 에 모두 등장
judge(LLM-as-judge·RAGAS) 모델은 JUDGE_MODEL(기본 Haiku 4.5)로 두 라운드 동안 고정한다.
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent import run_query  # noqa: E402
from src.config import LLM_MODEL, REGION, embeddings  # noqa: E402

EVAL_DIR = ROOT / "evaluation"
NON_TOOL_STEPS = ("guard", "refuse", "load_profile", "planner", "ask_user", "check", "rerank", "finalize", "propose_memory", "approve_memory")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
judge_llm = ChatBedrockConverse(
    model=JUDGE_MODEL,
    region_name=REGION,
    temperature=0,
    config=Config(retries={"mode": "adaptive", "max_attempts": 10}),
)


# ---------- LLM-as-judge ----------
class TraitCheck(BaseModel):
    trait: str
    met: bool
    why: str = Field(description="한 문장 근거")


class CaseJudgment(BaseModel):
    traits: list[TraitCheck] = Field(description="expected_traits 각각에 대한 판정 (입력 순서 유지)")
    forbidden_hits: list[str] = Field(description="답변에 실제로 나타난 forbidden 항목들 (없으면 빈 리스트)")


JUDGE_VERSION = "v2"  # v1(1차 최초 채점): contexts 미제공 → 출처 권위·사전 지식으로 오판 (round1_report J1·J2)
JUDGE_PROMPT = """당신은 피아노 학습 에이전트의 답변을 채점하는 엄격한 평가자입니다.
질문, 에이전트가 도구로 받은 근거(contexts), 에이전트 답변, 응답 경로(route),
기대 특성 목록(expected_traits), 금지 항목 목록(forbidden)이 주어집니다.

[판정 규칙]
- 각 expected_trait 마다 답변이 그 특성을 실제로 충족하는지 met=true/false 로 판정하세요.
- 사실(난이도 등급, 연도, 곡 존재 여부 등)이 맞는지는 **제공된 contexts 기준으로만** 판단하세요.
  당신의 사전 지식으로 contexts 내용을 반박하지 마세요. contexts 에 있는 값을 인용했다면 근거 있는 값입니다.
- '출처 표기'는 답변에 문서명·URL·'Henle' 등 출처가 명시되어 있으면 충족입니다. 출처의 권위(교재인지 위키인지)는 따지지 않습니다.
- 'DB에 있는 곡'·'DB 난이도'는 contexts 에 repertoire: 로 시작하는 레코드가 있고 답변이 그 값과 일치하면 충족입니다.
- forbidden 항목 중 답변에 실제로 나타난 것만 forbidden_hits 에 그대로 적으세요.
  거절·안내 문구 안에서 해당 주제를 언급만 하는 것은 위반이 아닙니다 (예: '병명을 진단할 수 없습니다'는 진단 아님).
  '근거 없는 ~'·'지어낸 ~' 류는 답변의 값이 contexts 에 없을 때만 위반입니다.
- 관대하게 추측하지 말고 답변 텍스트와 contexts 에 근거해 판단하세요."""

_judge = judge_llm.with_structured_output(CaseJudgment)


def split_items(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(";") if t.strip()]


def judge_case(case: dict, result: dict) -> dict:
    traits = split_items(case["expected_traits"])
    forbidden = split_items(case["forbidden"])
    ctx = "\n\n".join(
        f"[{c['doc_id']}] (출처: {c.get('source') or '자체 작성'})\n{c['text'][:1200]}" for c in result["contexts"]
    ) or "(없음)"
    msg = (
        f"질문: {case['input']}\n\ncontexts:\n{ctx[:15000]}\n\n"
        f"route: {result['route']}\n\n답변:\n{result['answer']}\n\n"
        f"expected_traits: {json.dumps(traits, ensure_ascii=False)}\n"
        f"forbidden: {json.dumps(forbidden, ensure_ascii=False)}"
    )
    j: CaseJudgment = _judge.invoke([("system", JUDGE_PROMPT), ("human", msg)])
    expected_tools = split_items(case["expected_tools"])
    called = {t["step"] for t in result["trace"]}
    missing_tools = [t for t in expected_tools if t not in called]
    traits_ok = all(t.met for t in j.traits) and len(j.traits) >= len(traits)
    forbidden_ok = not j.forbidden_hits
    return {
        "traits": [t.model_dump() for t in j.traits],
        "forbidden_hits": j.forbidden_hits,
        "missing_tools": missing_tools,
        "traits_ok": traits_ok,
        "forbidden_ok": forbidden_ok,
        "tools_ok": not missing_tools,
        "passed": traits_ok and forbidden_ok and not missing_tools,
    }


# ---------- RAGAS ----------
# v1: answer_relevancy 역생성 질문이 영어로 생성됨
# v2: + 역생성 질문 언어를 답변 언어에 맞춤
# v3: + context_precision 에서 '정답의 일부를 뒷받침하는 근거'도 useful(1)로 보도록 명시
RAGAS_VERSION = "v3"
LANG_HINT = (
    "\nWrite the generated question in the same language as the response "
    "(if the response is in Korean, the question must be in Korean)."
)
# 근거는 문서의 한 섹션이고 reference 는 여러 사실을 묶은 문장이라, Haiku judge 가 "일부만 뒷받침 → incomplete → 0" 으로
# 판정해 정답 근거까지 0점이 됐다 (#4 녹턴 작곡 시기 청크 등, round2_report 참고). RAGAS 의 원래 정의(답을 만드는 데
# 도움이 됐는가)에 맞게 부분 근거도 1로 보도록 명시한다. 1·2차 모든 결과를 이 설정으로 다시 계산한다.
PARTIAL_HINT = (
    "\nA context counts as useful (verdict 1) if it supports ANY part of the given answer, even if it does not "
    "cover the whole answer by itself. Give 0 only when the context contributes nothing to the answer."
)


def run_ragas(rows: list[dict]) -> tuple[dict, list[dict]]:
    # ragas 0.4.3 은 langchain-community 0.4 에서 제거된 vertexai 모듈을 import 하므로 빈 모듈로 대체한다.
    shim = types.ModuleType("langchain_community.chat_models.vertexai")
    shim.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules.setdefault(shim.__name__, shim)

    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    # answer_relevancy 는 답변에서 질문을 역생성해 원 질문과 임베딩 유사도를 잰다. 기본 프롬프트가 영어라
    # 한국어 답변에서도 영어 질문을 만들어 유사도가 0.07 수준으로 떨어졌다(평가기 결함, round2_report 참고).
    # 역생성 질문을 답변과 같은 언어로 만들게 한다. 1차·2차 모두 이 설정으로 다시 계산한다.
    qg = answer_relevancy.question_generation
    if LANG_HINT not in qg.instruction:
        qg.instruction += LANG_HINT
    cp = context_precision.context_precision_prompt
    if PARTIAL_HINT not in cp.instruction:
        cp.instruction += PARTIAL_HINT

    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": r["input"],
                "response": r["answer"],
                "retrieved_contexts": [c["text"] for c in r["contexts"]] or ["(검색 결과 없음)"],
                "reference": r["reference"],
            }
            for r in rows
        ]
    )
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_recall, context_precision],
        llm=LangchainLLMWrapper(judge_llm),
        embeddings=LangchainEmbeddingsWrapper(embeddings),
        run_config=RunConfig(max_workers=2, timeout=300, max_retries=5),
        show_progress=False,
    )
    df = result.to_pandas()
    names = ["faithfulness", "answer_relevancy", "context_recall", "context_precision"]
    per_case = []
    for r, (_, row) in zip(rows, df.iterrows()):
        per_case.append({"id": r["id"], **{n: _num(row.get(n)) for n in names}})
    means = {}
    for n in names:
        vals = [c[n] for c in per_case if c[n] is not None]
        means[n] = round(statistics.mean(vals), 4) if vals else None
    return means, per_case


def _num(v):
    try:
        f = float(v)
        return None if f != f else round(f, 4)  # NaN → None
    except (TypeError, ValueError):
        return None


def summarize(records: list[dict], ragas_means: dict) -> dict:
    by_cat: dict[str, list[int]] = {}
    for r in records:
        by_cat.setdefault(r["category"], [0, 0])
        by_cat[r["category"]][1] += 1
        by_cat[r["category"]][0] += int(r["verdict"]["passed"])
    return {
        "passed": sum(int(r["verdict"]["passed"]) for r in records),
        "total": len(records),
        "by_category": {k: f"{v[0]}/{v[1]}" for k, v in by_cat.items()},
        "failed_ids": [r["id"] for r in records if not r["verdict"]["passed"]],
        "ragas": ragas_means,
        "avg_latency_s": round(statistics.mean(r["latency_s"] for r in records), 1) if records else None,
    }


def rescore_ragas(path: Path) -> None:
    """저장된 결과 파일의 positive 답변에 대해 RAGAS 만 현재 설정(RAGAS_VERSION)으로 다시 계산한다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    references = json.loads((EVAL_DIR / "reference.json").read_text(encoding="utf-8"))
    rows = [
        {**r, "reference": references[r["id"]]}
        for r in data["cases"]
        if r["category"] == "positive" and r["id"] in references and r["answer"]
    ]
    means, per_case = run_ragas(rows)
    data["summary"].setdefault("ragas_original", data["summary"].get("ragas"))
    data["summary"]["ragas"] = means
    data["ragas_per_case"] = per_case
    data["meta"]["ragas_version"] = RAGAS_VERSION
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.name, json.dumps(means, ensure_ascii=False))


def rejudge(round_no: int) -> None:
    """저장된 답변을 현재 judge(JUDGE_VERSION)로 재채점한다. RAGAS 값은 원본 그대로 둔다."""
    src = EVAL_DIR / f"results_round{round_no}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in csv.DictReader(open(EVAL_DIR / "test_queries.csv", encoding="utf-8-sig"))}
    for rec in data["cases"]:
        result = {k: rec[k] for k in ("answer", "contexts", "trace", "route")}
        rec["verdict_original"] = rec["verdict"]
        rec["verdict"] = judge_case(cases[rec["id"]], result)
        mark = "PASS" if rec["verdict"]["passed"] else "FAIL"
        print(f"[{mark}] #{rec['id']:>2} (원래 {'PASS' if rec['verdict_original']['passed'] else 'FAIL'})", flush=True)
    data["summary_original"] = data["summary"]
    data["summary"] = summarize(data["cases"], data["summary"].get("ragas", {}))
    data["meta"]["rejudged_with"] = {"judge_model": JUDGE_MODEL, "judge_version": JUDGE_VERSION}
    out = EVAL_DIR / f"results_round{round_no}_rejudged.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
    print(f"saved → {out}")


# ---------- main ----------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--ids", default="", help="쉼표로 구분한 케이스 id (부분 실행)")
    ap.add_argument("--skip-ragas", action="store_true")
    ap.add_argument("--rejudge", action="store_true",
                    help="에이전트를 다시 돌리지 않고 results_roundN.json 의 저장된 답변을 현재 judge 로 재채점")
    ap.add_argument("--rescore-ragas", default="", help="RAGAS 만 다시 계산할 결과 파일 이름 (evaluation/ 기준)")
    ap.add_argument("--tag", default="", help="결과 파일 접미사 (예: sonnet46 → results_round2_sonnet46.json)")
    args = ap.parse_args()
    if args.rescore_ragas:
        rescore_ragas(EVAL_DIR / args.rescore_ragas)
        return
    if args.rejudge:
        rejudge(args.round)
        return

    cases = list(csv.DictReader(open(EVAL_DIR / "test_queries.csv", encoding="utf-8-sig")))
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    references = json.loads((EVAL_DIR / "reference.json").read_text(encoding="utf-8"))

    suffix = (f"_{args.tag}" if args.tag else "") + ("_partial" if args.ids else "")
    progress = EVAL_DIR / f"results_round{args.round}{suffix}.inprogress.json"
    records = []
    for case in cases:
        t0 = time.time()
        try:
            result = run_query(case["input"])
            error = ""
        except Exception as e:  # 한 케이스 실패가 전체 평가를 멈추지 않게 한다
            result = {"answer": "", "contexts": [], "trace": [], "route": "error", "refusal_reason": ""}
            error = f"{type(e).__name__}: {e}"
        latency = round(time.time() - t0, 1)
        verdict = judge_case(case, result) if not error else {"passed": False, "error": error}
        rec = {
            "id": case["id"],
            "category": case["category"],
            "input": case["input"],
            "route": result["route"],
            "answer": result["answer"],
            "tools_called": [t["step"] for t in result["trace"] if t["step"] not in NON_TOOL_STEPS],
            "contexts": result["contexts"],
            "trace": result["trace"],
            "latency_s": latency,
            "verdict": verdict,
        }
        records.append(rec)
        mark = "PASS" if verdict["passed"] else "FAIL"
        print(f"[{mark}] #{case['id']:>2} {case['category']:<9} route={result['route']:<7} "
              f"tools={rec['tools_called']} {latency}s" + (f"  ERROR {error[:200]}" if error else ""), flush=True)
        # 중간 저장: 실행이 끊겨도 진행분이 남도록 케이스마다 기록한다.
        progress.write_text(json.dumps({"model": LLM_MODEL, "cases": records}, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    ragas_means, ragas_cases = {}, []
    ragas_rows = [
        {**r, "reference": references[r["id"]]}
        for r in records
        if r["category"] == "positive" and r["id"] in references and r["answer"]
    ]
    if ragas_rows and not args.skip_ragas:
        print(f"RAGAS 평가 중 (positive {len(ragas_rows)}건)...", flush=True)
        ragas_means, ragas_cases = run_ragas(ragas_rows)

    summary = summarize(records, ragas_means)
    out = {
        "meta": {
            "round": args.round,
            "agent_model": LLM_MODEL,
            "judge_model": JUDGE_MODEL,
            "judge_version": JUDGE_VERSION,
            "ragas_version": RAGAS_VERSION,
            "embedding_model": os.getenv("EMBED_MODEL", "amazon.titan-embed-text-v2:0"),
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "partial": bool(args.ids),
        },
        "summary": summary,
        "ragas_per_case": ragas_cases,
        "cases": records,
    }
    path = EVAL_DIR / f"results_round{args.round}{suffix}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    progress.unlink(missing_ok=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved → {path}")


if __name__ == "__main__":
    main()
