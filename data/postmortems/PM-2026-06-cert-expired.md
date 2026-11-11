# PM-2026-06 auth 도메인 인증서 만료

- 발생일: 2026-06-02 00:00 ~ 00:41 (41분)
- 심각도: SEV1
- 영향 서비스: auth-api (auth.shop.example.com)
- 관련 런북: RB-005

## 증상
- 자정 이후 앱 로그인 전면 실패, `x509: certificate has expired`

## 근본 원인
- cert-manager의 ACME HTTP-01 챌린지가 WAF 정책 변경으로 차단되어 30일간 갱신 실패
- `CertExpiringSoon` 알람이 미사용 채널로 라우팅되어 아무도 인지하지 못함

## 조치
1. 00:15 보안운영팀 온콜 호출, DNS-01 방식으로 수동 갱신
2. 00:41 게이트웨이 리로드 후 정상화

## 재발 방지
- 인증서 알람을 #sre-alert 및 보안운영팀 온콜로 라우팅
- WAF 정책 변경 시 ACME 경로 예외 체크리스트 추가
