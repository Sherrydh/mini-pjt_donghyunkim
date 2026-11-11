# RB-005 TLS 인증서 만료

- 적용 서비스: 외부 노출 도메인 전체 (api.shop.example.com, auth.shop.example.com)
- 심각도 기준: 만료 14일 전 경고, 만료 시 SEV1
- 최종 수정: 2026-06-10 / 작성: 보안운영팀

## 증상
- 알람 `CertExpiringSoon` (14일 이하)
- 클라이언트 오류 `SSL_ERROR_EXPIRED_CERT_ALERT`, `x509: certificate has expired`
- 앱 로그인/결제 전면 실패

## 1차 확인
1. `openssl s_client -connect <도메인>:443 | openssl x509 -noout -dates` 로 만료일 확인
2. cert-manager `Certificate` 리소스 상태(Ready) 확인
3. 갱신 실패 이벤트(ACME challenge 실패 등) 확인

## 조치
1. cert-manager 자동 갱신 실패 시 보안운영팀 온콜에게 수동 갱신 요청
2. 인증서 개인키는 보안운영팀만 취급. **개인키를 채팅·티켓에 붙여넣지 않는다**
3. 갱신 후 게이트웨이 리로드, 외부에서 재확인

## 에스컬레이션
- 만료 상태 발견 즉시 SEV1, 보안운영팀 온콜 즉시 호출
