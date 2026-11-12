"""SERVICE.md §5 '쓸만하다' 지표를 저장된 평가 결과에서 계산한다 (LLM 호출 없음, 결정적).

1) 난이도 환각: 답변에 나온 "Henle 난이도 N" 이 그 곡의 곡 DB 값(전곡·악장별 등급)과 일치하는가.
   - 답변의 각 줄에서 등급 표기 앞에 가장 가까이 언급된 곡을 찾아 짝지운다.
   - 곡을 특정할 수 없는 등급 표기(예: "Henle 1~2 곡이 적당")는 '곡 미지정'으로 따로 센다 (환각 판정 대상 아님).
2) 출처 표기율: 사실성 답변(positive·edge 중 route=answer)이 출처를 포함하는가.
   - '출처' 줄, http URL, 'Henle' 출처 언급 중 하나라도 있으면 표기한 것으로 본다.

    python -m evaluation.measure_service_metrics
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "evaluation"
DB = json.loads((ROOT / "data" / "repertoire.json").read_text(encoding="utf-8"))

GRADE = re.compile(r"Henle\s*(?:난이도)?\s*(\d)(?!\s*[~\-–]\s*\d)")
CATALOG = re.compile(
    r"(Op\.?\s*\d+(?:\s*(?:No\.?\s*|-)\d+)?|K\.?\s*\d+[a-z]?|BWV\s*(?:Anh\.?\s*)?\d+|WoO\s*\d+|KK\s*[IVXa-z]+/\d+|Hob\.?\s*XVI:\d+)",
    re.IGNORECASE,
)


def norm(s: str) -> str:
    s = re.sub(r"(op\.?\s*\d+)\s*-\s*(\d+)", r"\1no\2", s.lower())  # 'Op.12-2' 와 'Op.12 No.2' 를 같은 키로
    return re.sub(r"[\s\.\-_,'\"“”()·/]", "", s)


def context_words(p: dict) -> list[str]:
    """동명 작품번호(예: 쇼팽 Op.68 No.2 / 슈만 Op.68-2)를 가르기 위한 작곡가·제목 단어."""
    words = [p.get("composer_ko", "").split()[-1] if p.get("composer_ko") else ""]
    words += re.findall(r"[가-힣]{2,}", re.sub(r"\(.*?\)", "", p.get("title_ko", "")))
    return [norm(w) for w in words if len(w) >= 2]


def piece_keys(p: dict) -> list[str]:
    keys = set()
    for text in [p.get("title", ""), p.get("title_ko", ""), *p.get("aliases", [])]:
        for m in CATALOG.finditer(text):
            keys.add(norm(m.group(1)))
    for text in [p.get("title_ko", ""), *p.get("aliases", [])]:
        for q in re.findall(r"[\"“]([^\"”]+)[\"”]", text):  # 별칭 따옴표 안 (예: "엘리제를 위하여")
            keys.add(norm(q))
    for a in p.get("aliases", []):
        if len(norm(a)) >= 4 and not CATALOG.search(a):
            keys.add(norm(a))
    return [k for k in keys if len(k) >= 3]


KEYS = [(k, p) for p in DB for k in piece_keys(p)]
# 같은 키가 여러 곡에 있으면(예: 'op36') 더 긴 키가 먼저 맞도록 길이순 정렬
KEYS.sort(key=lambda kp: -len(kp[0]))


def allowed_levels(p: dict) -> set[int]:
    lv = {p["level"]}
    lv |= set(p.get("level_range") or [])
    lv |= {m["level"] for m in p.get("movements", [])}
    return lv


def last_piece_in(segment: str) -> dict | None:
    """segment 에서 마지막으로 언급된 곡을 찾는다. 같은 위치에 여러 곡이 맞으면 작곡가·제목 단어로 고른다."""
    seg = norm(segment)
    best_pos, cands = -1, []
    for key, p in KEYS:
        pos = seg.rfind(key)
        if pos < 0:
            continue
        end = pos + len(key)
        if end > best_pos:
            best_pos, cands = end, [p]
        elif end == best_pos and p not in cands:
            cands.append(p)
    if not cands:
        return None
    return max(cands, key=lambda p: sum(w in seg for w in context_words(p)))


def grade_claims(answer: str) -> list[dict]:
    claims = []
    for line in answer.splitlines():
        prev_end = 0
        for m in GRADE.finditer(line):
            # 직전 등급 표기 이후(같은 구절)에 언급된 곡만 짝짓는다. 없으면 '곡 미지정'(예: "다음 단계는 Henle 4")
            p = last_piece_in(line[prev_end:m.start()])
            n = int(m.group(1))
            claims.append(
                {
                    "text": line.strip()[:160],
                    "grade": n,
                    "piece": p["id"] if p else None,
                    "db_levels": sorted(allowed_levels(p)) if p else None,
                    "ok": (n in allowed_levels(p)) if p else None,
                }
            )
            prev_end = m.end()
    return claims


def has_source(answer: str) -> bool:
    return bool(re.search(r"출처|https?://|Henle", answer))


def measure(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    claims, factual, cited, uncited_ids = [], 0, 0, []
    for c in data["cases"]:
        if c.get("route") in ("answer", "limited"):
            for cl in grade_claims(c["answer"]):
                claims.append({"id": c["id"], **cl})
        if c["category"] in ("positive", "edge") and c.get("route") == "answer":
            factual += 1
            if has_source(c["answer"]):
                cited += 1
            else:
                uncited_ids.append(c["id"])
    attributed = [x for x in claims if x["piece"]]
    wrong = [x for x in attributed if not x["ok"]]
    return {
        "file": path.name,
        "grade_mentions": len(claims),
        "grade_claims_with_piece": len(attributed),
        "grade_claims_without_piece": len(claims) - len(attributed),
        "hallucinated_grades": len(wrong),
        "hallucinations": wrong,
        "factual_answers": factual,
        "cited_answers": cited,
        "citation_rate": round(cited / factual, 3) if factual else None,
        "uncited_ids": uncited_ids,
    }


def main() -> None:
    files = sys.argv[1:] or [
        "results_round1_rejudged.json",
        "results_round2_v12_run1.json",
        "results_round2_v12_run2.json",
        "results_round2_v12_run3.json",
    ]
    out = [measure(EVAL_DIR / f) for f in files]
    for r in out:
        print(
            f"{r['file']:34s} 등급언급 {r['grade_mentions']:3d} (곡 지정 {r['grade_claims_with_piece']:3d}) "
            f"환각 {r['hallucinated_grades']}  출처 {r['cited_answers']}/{r['factual_answers']} = {r['citation_rate']}"
            + (f"  미표기 {r['uncited_ids']}" if r["uncited_ids"] else "")
        )
        for h in r["hallucinations"]:
            print(f"    ✗ #{h['id']} {h['piece']} 답변={h['grade']} DB={h['db_levels']} | {h['text']}")
    (EVAL_DIR / "service_metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved → evaluation/service_metrics.json")


if __name__ == "__main__":
    main()
