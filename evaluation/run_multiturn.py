"""대화형(여러 턴) 평가: 되묻기 → 사용자 답 → 맞춤 답 → 저장 승인 → 기억 재사용.

단발형 인-아웃 세트(test_queries.csv)는 질문 하나에 답 하나라, 에이전트가 되물어도 답해 줄 사람이 없다.
이 스크립트는 multiturn_cases.json 의 사용자 메시지를 순서대로 보내 실제 사용 흐름을 재현하고,
최종 답은 run_eval 과 같은 judge(LLM-as-judge)로, 대화 동작(되묻기·저장 승인·기억)은 코드로 확인한다.

    python -m evaluation.run_multiturn
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.run_eval import EVAL_DIR, JUDGE_MODEL, JUDGE_VERSION, judge_case  # noqa: E402
from src import memory  # noqa: E402
from src.agent import run_query  # noqa: E402
from src.config import LLM_MODEL  # noqa: E402


def run_case(case: dict) -> dict:
    user_id = f"mt-{case['id']}-{uuid.uuid4().hex[:6]}"
    thread_id = f"{user_id}-t1"
    responses = []
    t0 = time.time()
    try:
        for turn in case["turns"]:
            if turn.get("new_thread"):
                thread_id = f"{user_id}-t{len(responses) + 1}"
            if "resume" in turn:
                r = run_query(thread_id=thread_id, resume=turn["resume"], user_id=user_id)
            else:
                r = run_query(question=turn["question"], thread_id=thread_id, user_id=user_id)
            responses.append(r)
        error = ""
    except Exception as e:  # 할당량 등 실행 오류도 기록한다
        error = f"{type(e).__name__}: {e}"
    latency = round(time.time() - t0, 1)
    profile = memory.get_profile(user_id)
    memory.delete_profile(user_id)

    checks: dict[str, bool] = {}
    exp = case.get("expect", {})
    for key, want in exp.items():
        if key.startswith("turn") and key.endswith("_route"):
            idx = int(key[4]) - 1
            checks[key] = idx < len(responses) and responses[idx]["route"] == want
        elif key.startswith("turn") and key.endswith("_pending_save"):
            idx = int(key[4]) - 1
            checks[key] = idx < len(responses) and bool(responses[idx].get("pending_action")) == want
        elif key.startswith("turn") and key.endswith("_profile_loaded"):
            idx = int(key[4]) - 1
            load = [t for t in responses[idx]["trace"] if t["step"] == "load_profile"] if idx < len(responses) else []
            checks[key] = bool(load) and want in load[0]["output"]
        elif key == "saved_level_contains":
            checks[key] = want in (profile.get("level") or "")
        elif key == "saved_goal_contains":
            checks[key] = want in (profile.get("goal") or "")

    verdict = {"passed": False, "error": error}
    if not error and len(responses) >= case["judge_turn"]:
        final = responses[case["judge_turn"] - 1]
        judge_input = {
            "input": " → ".join(t.get("question") or f"[승인응답:{t.get('resume')}]" for t in case["turns"][: case["judge_turn"]]),
            "expected_traits": case["expected_traits"],
            "forbidden": case.get("forbidden", ""),
            "expected_tools": case.get("expected_tools", ""),
        }
        verdict = judge_case(judge_input, final)
    behavior_ok = all(checks.values()) if checks else True
    return {
        "id": case["id"],
        "title": case["title"],
        "turns": [
            {
                "sent": t,
                "route": r["route"],
                "status": r["status"],
                "pending_action": r.get("pending_action"),
                "answer": r["answer"],
                "trace_steps": [s["step"] for s in r["trace"]],
            }
            for t, r in zip(case["turns"], responses)
        ],
        "final_contexts": responses[case["judge_turn"] - 1]["contexts"] if len(responses) >= case["judge_turn"] else [],
        "saved_profile": profile,
        "behavior_checks": checks,
        "behavior_ok": behavior_ok,
        "answer_verdict": verdict,
        "passed": behavior_ok and verdict.get("passed", False),
        "latency_s": latency,
    }


def main() -> None:
    cases = json.loads((EVAL_DIR / "multiturn_cases.json").read_text(encoding="utf-8"))["cases"]
    records = []
    for case in cases:
        rec = run_case(case)
        records.append(rec)
        print(
            f"[{'PASS' if rec['passed'] else 'FAIL'}] {rec['id']} {rec['title']} | "
            f"behavior={rec['behavior_checks']} answer={'PASS' if rec['answer_verdict'].get('passed') else 'FAIL'} "
            f"{rec['latency_s']}s" + (f" ERROR {rec['answer_verdict'].get('error','')[:150]}" if rec["answer_verdict"].get("error") else ""),
            flush=True,
        )
    out = {
        "meta": {
            "agent_model": LLM_MODEL,
            "judge_model": JUDGE_MODEL,
            "judge_version": JUDGE_VERSION,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "summary": {
            "passed": sum(r["passed"] for r in records),
            "total": len(records),
            "behavior_ok": sum(r["behavior_ok"] for r in records),
            "answer_ok": sum(bool(r["answer_verdict"].get("passed")) for r in records),
            "failed_ids": [r["id"] for r in records if not r["passed"]],
        },
        "cases": records,
    }
    path = EVAL_DIR / "results_multiturn.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    print(f"saved → {path}")


if __name__ == "__main__":
    main()
