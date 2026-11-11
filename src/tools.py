"""도메인 도구 4종.

- search_technique        : 연습법·테크닉 코퍼스 하이브리드 검색 (RAG)
- search_music_knowledge  : 작곡가·작품·음악사 코퍼스 하이브리드 검색 (RAG)
- find_pieces             : 곡 난이도 DB 조건 필터 (다음 곡 추천)
- get_piece               : 곡 이름으로 난이도·필요 테크닉 조회

도구가 읽은 근거는 요청 단위 수집기(contextvar)에 쌓여 API 응답의 contexts 가 된다.
"""
import json
import re
from contextvars import ContextVar
from functools import lru_cache

from langchain_core.tools import tool

from src.config import REPERTOIRE_PATH
from src.retriever import retrieve

# 요청마다 새 리스트로 초기화된다 (agent.run_query 참고)
_collected: ContextVar[list | None] = ContextVar("collected_contexts", default=None)


def start_collection() -> list:
    bucket: list = []
    _collected.set(bucket)
    return bucket


def _collect(items: list[dict]) -> None:
    bucket = _collected.get()
    if bucket is None:
        return
    seen = {c["doc_id"] for c in bucket}
    for it in items:
        if it["doc_id"] not in seen:
            bucket.append(it)
            seen.add(it["doc_id"])


def _format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "검색 결과 없음"
    return "\n\n".join(
        f"[doc_id: {c['doc_id']}] (출처: {c['source'] or '자체 작성'})\n{c['text']}" for c in chunks
    )


# ---------- RAG 도구 ----------
@tool
def search_technique(query: str) -> str:
    """피아노 연습법·테크닉 문서를 검색합니다. 스케일, 엄지 넘기기, 아르페지오, 페달, 옥타브, 하농,
    체르니 등 연습곡 진도, 연습 루틴, 이완·부상 예방 질문에 사용하세요. query는 핵심 키워드를 담은 한국어 문장."""
    chunks = retrieve(query, "technique")
    _collect(chunks)
    return _format_chunks(chunks)


@tool
def search_music_knowledge(query: str) -> str:
    """작곡가·작품·음악사 문서를 검색합니다. 작품의 작곡 배경, 별칭의 유래, 형식, 감상 포인트,
    작곡가 생애·작품 목록, 바로크·고전·낭만 시대 특징 질문에 사용하세요."""
    chunks = retrieve(query, "knowledge")
    _collect(chunks)
    return _format_chunks(chunks)


# ---------- 곡 난이도 DB 도구 ----------
@lru_cache(maxsize=1)
def load_repertoire() -> list[dict]:
    return json.loads(REPERTOIRE_PATH.read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return re.sub(r"[\s\.\-_,'\"()·/]", "", str(text).lower())


def _piece_record(p: dict) -> dict:
    """도구 결과이자 RAGAS context 로 쓰일 곡 레코드 텍스트."""
    lines = [
        f"곡: {p.get('composer_ko', '')} ({p.get('composer', '')}) — {p.get('title_ko', '')} / {p.get('title', '')}",
        f"시대: {p.get('era', '')}",
        f"Henle 난이도: {p.get('level')} (범위 {p.get('level_range')})",
    ]
    if p.get("movements"):
        mv = ", ".join(f"{m.get('name')}: {m.get('level')}" for m in p["movements"])
        lines.append(f"악장별 난이도: {mv}")
    if p.get("techniques"):
        lines.append(f"필요 테크닉: {', '.join(p['techniques'])}")
    if p.get("prerequisites"):
        lines.append(f"먼저 익히면 좋은 곡: {', '.join(p['prerequisites'])}")
    if p.get("notes"):
        lines.append(f"메모: {p['notes']}")
    lines.append(f"난이도 출처: {p.get('level_source', '')}")
    return {
        "doc_id": f"repertoire:{p['id']}",
        "text": "\n".join(lines),
        "source": p.get("level_source", ""),
        "title": p.get("title_ko") or p.get("title", ""),
    }


def _search_keys(p: dict) -> list[str]:
    keys = [p.get("title", ""), p.get("title_ko", ""), p.get("opus", ""), p["id"], *p.get("aliases", [])]
    return [_norm(k) for k in keys if k]


def match_pieces(query: str) -> list[tuple[float, dict]]:
    """곡 이름 질의와 DB 곡을 매칭해 (점수, 곡) 목록을 점수순으로 반환한다."""
    q = _norm(query)
    tokens = [_norm(t) for t in re.split(r"[\s,/]+", query) if len(_norm(t)) >= 2]
    scored = []
    for p in load_repertoire():
        keys = _search_keys(p)
        blob = "".join(keys) + _norm(p.get("composer", "")) + _norm(p.get("composer_ko", ""))
        score = 0.0
        if any(k and (k in q or q in k) for k in keys):
            score += 3.0
        if tokens:
            score += sum(1 for t in tokens if t in blob) / len(tokens) * 2
        if score >= 1.5:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return scored


@tool
def get_piece(title: str) -> str:
    """곡 이름(한국어·영어·작품번호 모두 가능, 예: '모차르트 K.545', '월광 소나타', 'Clair de lune')으로
    곡 난이도 DB를 조회합니다. Henle 난이도 등급(1~9), 악장별 난이도, 필요 테크닉, 먼저 익힐 곡, 출처를 돌려줍니다.
    DB에 없으면 '자료 없음'을 돌려줍니다. 난이도를 말할 때는 반드시 이 도구 결과만 사용하세요."""
    matches = match_pieces(title)
    if not matches:
        return f"자료 없음: '{title}'에 해당하는 곡이 난이도 DB에 없습니다. 난이도를 추정하지 마세요."
    top = [p for s, p in matches if s >= matches[0][0] - 0.5][:3]
    records = [_piece_record(p) for p in top]
    _collect(records)
    return "\n\n".join(f"[doc_id: {r['doc_id']}]\n{r['text']}" for r in records)


@tool
def find_pieces(level_min: int = 1, level_max: int = 9, era: str = "", technique: str = "", limit: int = 8) -> str:
    """곡 난이도 DB에서 조건에 맞는 곡 목록을 찾습니다(다음 곡 추천용).
    level_min/level_max: Henle 난이도 범위(1~9). 사용자가 방금 끝낸 곡이 있으면 먼저 get_piece로 그 곡의 등급을 확인한 뒤
    그 등급~+1 범위로 찾으세요. era: baroque|classical|romantic|impressionist|modern (선택).
    technique: '스케일', '아르페지오', '옥타브', '페달', '알베르티 베이스' 같은 테크닉 키워드 (선택)."""
    rows = []
    for p in load_repertoire():
        lvl = p.get("level")
        if lvl is None or not (level_min <= lvl <= level_max):
            continue
        if era and p.get("era", "").lower() != era.lower():
            continue
        if technique and not any(technique.replace(" ", "") in t.replace(" ", "") for t in p.get("techniques", [])):
            continue
        rows.append(p)
    rows.sort(key=lambda p: (p.get("level", 0), p.get("composer", "")))
    rows = rows[:limit]
    if not rows:
        return "조건에 맞는 곡이 난이도 DB에 없습니다. 조건을 넓혀 다시 찾거나 '자료 없음'이라고 답하세요."
    records = [_piece_record(p) for p in rows]
    _collect(records)
    return "\n\n".join(f"[doc_id: {r['doc_id']}]\n{r['text']}" for r in records)


TOOLS = [search_technique, search_music_knowledge, get_piece, find_pieces]
