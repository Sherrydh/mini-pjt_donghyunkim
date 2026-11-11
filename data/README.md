# data/

> 모든 데이터는 **교육용 가짜 데이터**입니다. 실제 사내 시스템·인물·고객과 무관합니다.

| 경로 | 내용 | 사용처 |
|---|---|---|
| `runbooks/*.md` | 장애 유형별 대응 런북 | `search_runbook` (RAG) |
| `postmortems/*.md` | 과거 장애 회고 | `search_postmortem` (RAG) |
| `logs/app.log` | 서비스 애플리케이션 로그 (PII 일부 포함 → 도구에서 마스킹) | `query_logs` |
| `metrics.json` | 서비스별 현재 메트릭과 임계치 | `get_metrics` |
| `oncall.json` | 서비스별 온콜 담당자·에스컬레이션 경로 | `get_oncall` |

가상 서비스: `order-api`, `payment-api`, `auth-api`, `notification-worker`, `search-api`
