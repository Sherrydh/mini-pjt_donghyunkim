# RB-004 메모리 부족(OOMKilled)·메모리 누수

- 적용 서비스: 전 서비스 (JVM 기반: order-api, payment-api)
- 심각도 기준: 파드 재시작 10분 내 3회 이상 SEV3, 가용 파드 50% 미만 SEV2
- 최종 수정: 2026-08-20 / 작성: SRE팀

## 증상
- 파드 상태 `OOMKilled`, `CrashLoopBackOff`
- 메트릭 `memory_usage_percent` 가 계단식으로 증가 후 재시작 반복 (누수 패턴)
- JVM 로그 `java.lang.OutOfMemoryError: Java heap space`

## 1차 확인
1. `get_metrics` 로 `memory_usage_percent`, `pod_restart_count` 확인
2. 증가 패턴이 배포 시점부터인지 확인 (누수는 배포 기인이 대부분)
3. 가능하면 재시작 전 힙 덤프 확보: `jcmd <pid> GC.heap_dump` (디스크 여유 확인 후)

## 조치
1. 배포 기인 누수: RB-007 롤백
2. 트래픽 기인 일시 증가: 메모리 limit 상향은 노드 여유 확인 후 온콜 승인
3. 힙 덤프는 개발팀에 전달하여 근본 원인 분석

## 주의
- 힙 덤프 파일은 고객 데이터(PII)가 포함될 수 있으므로 사내 보안 스토리지에만 저장, 메신저 공유 금지
