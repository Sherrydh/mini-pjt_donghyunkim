# RB-001 DB 커넥션 풀 고갈

- 적용 서비스: order-api, payment-api
- 심각도 기준: 에러율 5% 이상이면 SEV2, 결제 실패 동반 시 SEV1
- 최종 수정: 2026-09-02 / 작성: 플랫폼운영팀

## 증상
- 로그에 `HikariPool-1 - Connection is not available, request timed out after 30000ms` 출력
- API p99 응답시간 급증 (평소 300ms → 3초 이상), 이어서 5xx 증가
- 메트릭 `db_pool_active` 가 `db_pool_max` 에 붙어서 내려오지 않음

## 1차 확인 (5분 이내)
1. `get_metrics` 로 `db_pool_active`, `db_pool_max`, `db_slow_query_count` 확인
2. 최근 30분 내 배포 여부 확인 (배포 직후라면 RB-007 롤백 절차 우선 검토)
3. DB 서버 측 `SHOW PROCESSLIST` 로 장시간 실행 쿼리(30초 이상) 확인 — **조회만, KILL 금지**

## 조치
1. 슬로우 쿼리가 원인이면: DBA 온콜에게 쿼리 ID 전달 후 KILL 여부는 DBA가 결정
2. 트래픽 급증이 원인이면: 파드 수평 확장(HPA max 상향)은 서비스 온콜 승인 후 진행
3. 커넥션 누수(close 누락) 의심 시: 최근 배포 diff 확인 → 롤백 검토
4. 임시 조치로 풀 사이즈를 늘리는 것은 DB max_connections(기본 500)를 넘지 않는 범위에서만 허용

## 금지 사항
- 운영 DB 재시작 금지 (DBA 팀장 승인 필요)
- 운영 DB에서 DDL/DELETE 직접 실행 금지

## 에스컬레이션
- 15분 내 미해결 시 DBA 온콜 호출, 30분 내 미해결 시 SEV1 격상
