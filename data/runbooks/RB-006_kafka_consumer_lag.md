# RB-006 Kafka 컨슈머 랙(Consumer Lag) 증가

- 적용 서비스: notification-worker (토픽: order-events, payment-events)
- 심각도 기준: 랙 10,000 이상 10분 지속 SEV3, 100,000 이상 SEV2
- 최종 수정: 2026-09-28 / 작성: 메시징플랫폼팀

## 증상
- 알람 `KafkaConsumerLagHigh`
- 주문 완료 알림(문자·푸시) 지연 고객 문의 증가
- 메트릭 `kafka_consumer_lag` 지속 증가, `consume_rate` 감소

## 1차 확인
1. `get_metrics` 로 `kafka_consumer_lag`, `consume_rate`, `pod_restart_count` 확인
2. 컨슈머 그룹 리밸런싱 반복 여부 확인 (로그 `Revoking previously assigned partitions`)
3. 다운스트림(문자 발송 외부 API) 지연 여부 `query_logs` 로 확인

## 조치
1. 처리 지연이 외부 API 기인: 재시도 백오프 확인, 외부 업체 장애 공지 확인
2. 컨슈머 부족: 파티션 수 이내로 레플리카 확장 (파티션 12개 → 최대 12 레플리카)
3. 리밸런싱 반복: `max.poll.interval.ms` 초과 여부 확인, 배치 크기 축소

## 금지 사항
- 컨슈머 그룹 오프셋 리셋(skip)은 메시지 유실을 일으키므로 **메시징플랫폼팀 승인 필수**
- 토픽 삭제·재생성 금지
