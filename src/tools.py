"""장애 대응 도메인 도구.

원칙: 모든 도구는 '조회'만 한다. 재시작·삭제·롤백 같은 실행형 도구는 의도적으로 만들지 않는다.
검색된 문서는 CONTEXT_SINK 에 쌓여 API 응답의 contexts 로 반환된다.
"""
import json
import re
from contextvars import ContextVar

from langchain_core.tools import tool

from src.config import DATA_DIR
from src.retriever import retrieve

# 요청 단위로 검색된 컨텍스트를 모은다 (agent.run_query 에서 초기화)
CONTEXT_SINK: ContextVar[list | None] = ContextVar("CONTEXT_SINK", default=None)

SERVICES = ["order-api", "payment-api", "auth-api", "notification-worker", "search-api"]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
_CARD = re.compile(r"\b\d{4}-[\d*]{4}-[\d*]{4}-\d{4}\b")


def mask_pii(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    return _CARD.sub("[CARD]", text)


def _collect(chunks: list[dict]) -> None:
    sink = CONTEXT_SINK.get()
    if sink is not None:
        seen = {c["doc_id"] for c in sink}
        sink.extend(c for c in chunks if c["doc_id"] not in seen)


def _format(chunks: list[dict]) -> str:
    return "\n\n".join(f"<doc id=\"{c['doc_id']}\">\n{c['text']}\n</doc>" for c in chunks)


@tool
def search_runbook(query: str) -> str:
    """장애 대응 런북에서 증상·조치 절차·금지 사항·에스컬레이션 기준을 검색합니다.
    장애 증상, 알람 이름, 에러 메시지, '어떻게 조치하나' 류 질문에 가장 먼저 사용하세요."""
    chunks = retrieve(query, source="runbook", k=4)
    _collect(chunks)
    return _format(chunks) if chunks else "관련 런북을 찾지 못했습니다."


@tool
def search_postmortem(symptom: str) -> str:
    """과거 장애 회고(포스트모템)에서 유사 사례의 근본 원인·조치·재발 방지책을 검색합니다.
    '전에도 이런 적 있었나', 원인 후보를 좁힐 때 사용하세요."""
    chunks = retrieve(symptom, source="postmortem", k=3)
    _collect(chunks)
    return _format(chunks) if chunks else "유사한 과거 장애가 없습니다."


@tool
def query_logs(service: str, level: str = "", keyword: str = "", limit: int = 10) -> str:
    """서비스 애플리케이션 로그를 조회합니다. 개인정보(이메일·전화번호·카드번호)는 자동 마스킹됩니다.
    service: order-api, payment-api, auth-api, notification-worker, search-api 중 하나
    level: ERROR / WARN / INFO (선택), keyword: 포함 문자열 (선택)"""
    if service not in SERVICES:
        return f"알 수 없는 서비스 '{service}'. 조회 가능: {SERVICES}"
    lines = (DATA_DIR / "logs" / "app.log").read_text(encoding="utf-8").splitlines()
    hits = []
    for line in lines:
        parts = line.split(maxsplit=3)
        if len(parts) < 4 or parts[2] != service:
            continue
        if level and parts[1] != level.upper():
            continue
        if keyword and keyword.lower() not in line.lower():
            continue
        hits.append(mask_pii(line))
    if not hits:
        return f"{service} 에서 조건에 맞는 로그가 없습니다."
    return "\n".join(hits[-limit:])


@tool
def get_metrics(service: str, metric: str = "") -> str:
    """서비스의 현재 메트릭(5xx 비율, p99 지연, DB 풀, 메모리, 디스크, 컨슈머 랙, 최근 배포 등)과 임계치 초과 여부를 조회합니다.
    metric 을 비우면 해당 서비스의 전체 메트릭을 반환합니다."""
    data = json.loads((DATA_DIR / "metrics.json").read_text(encoding="utf-8"))
    if service not in data:
        return f"알 수 없는 서비스 '{service}'. 조회 가능: {SERVICES}"
    items = data[service]
    if metric:
        if metric not in items:
            return f"{service} 에 '{metric}' 메트릭이 없습니다. 사용 가능: {list(items)}"
        items = {metric: items[metric]}
    rows = []
    for name, m in items.items():
        v, th = m["value"], m["threshold"]
        flag = ""
        if isinstance(v, (int, float)) and th is not None:
            breach = v < th if "success_rate" in name else v > th
            flag = " ⚠ 임계치 초과" if breach else " (정상)"
        rows.append(f"- {name}: {v}" + (f" / 임계치 {th}" if th is not None else "") + flag)
    return f"[{service} 메트릭 · 기준 {data['_note'].split('기준 시각 ')[-1]}]\n" + "\n".join(rows)


@tool
def get_oncall(service: str) -> str:
    """서비스(또는 'database')의 온콜 담당자와 에스컬레이션 경로를 조회합니다. 메신저 핸들만 제공합니다."""
    data = json.loads((DATA_DIR / "oncall.json").read_text(encoding="utf-8"))
    if service not in data or service.startswith("_"):
        return f"'{service}' 온콜 정보가 없습니다. 조회 가능: {[k for k in data if not k.startswith('_')]}"
    o = data[service]
    return f"[{service}] 1차: {o['primary']} → 2차: {o['secondary']} → 에스컬레이션: {o['escalation']}"


TOOLS = [search_runbook, search_postmortem, query_logs, get_metrics, get_oncall]
