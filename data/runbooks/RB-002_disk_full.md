# RB-002 디스크 사용률 임계치 초과 (Disk Full)

- 적용 서비스: 전 서비스 공통 (특히 notification-worker, search-api)
- 심각도 기준: 사용률 85% 경고, 95% 이상 SEV2
- 최종 수정: 2026-07-15 / 작성: 플랫폼운영팀

## 증상
- 알람 `DiskUsageHigh` (node_filesystem 사용률 85% 초과)
- 로그에 `No space left on device` 또는 `IOException: write failed`
- 로그 수집 중단, 컨테이너 재시작 반복

## 1차 확인
1. `get_metrics` 로 `disk_usage_percent` 확인
2. `du -sh /var/log/* | sort -h | tail` 로 대용량 디렉터리 확인 (조회 명령)
3. 로그 로테이션(logrotate) 정상 동작 여부 확인

## 조치
1. 7일 이상 지난 압축 로그(`*.gz`)만 정리 대상. 삭제 전 목록을 온콜 채널에 공유
2. 현재 기록 중인 로그 파일은 삭제하지 말고 `truncate` 도 사용하지 않는다 (프로세스 fd 유지 문제)
3. Elasticsearch(search-api) 인덱스 정리는 ILM 정책으로만 수행, 수동 인덱스 삭제 금지
4. 근본 원인이 로그 폭증이면 해당 서비스의 로그 레벨(DEBUG 여부) 확인

## 금지 사항
- `rm -rf /var/log/*` 등 와일드카드 일괄 삭제 금지
- 데이터 볼륨(/data) 임의 삭제 금지

## 에스컬레이션
- 95% 초과 후 10분 내 해소 불가 시 인프라 온콜 호출
