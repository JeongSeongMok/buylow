---
title: "buylow — 한국 주식 자동매매·자동 트레이딩 툴킷 (LEAN 백테스트·KIS 실거래)"
description: "한국 주식(KOSPI·KOSDAQ) 자동매매 전략을 코드 없이 만들고, 과거 데이터로 백테스트하고, 한국투자증권(KIS) API로 실거래까지 돌리는 오픈소스 툴킷. QuantConnect LEAN 엔진 기반으로 백테스트와 라이브가 동일 코드."
---

# buylow — 한국 주식 자동매매 / 자동 트레이딩 툴킷

**buylow**는 한국 주식(KOSPI·KOSDAQ)의 **자동매매·자동 트레이딩 전략을 코드 없이** 구성하고,
과거 데이터로 **백테스트**하고, **한국투자증권(KIS) API**로 **실거래(라이브)**까지 돌리는
오픈소스 툴킷입니다. [QuantConnect **LEAN**](https://github.com/QuantConnect/Lean) 엔진 위에서 동작하므로,
**같은 전략 정의가 백테스트와 실거래에서 그대로 실행**됩니다. 모든 데이터·API 키는 내 PC에만 저장됩니다.

> 한국 주식 자동매매 봇 · 주식 백테스트 · 트레이딩 봇 · LEAN 한국 주식 · 한국투자증권 자동매매 · pykrx · 알고리즘 트레이딩

<p>
  <a href="https://github.com/JeongSeongMok/buylow">⭐ GitHub 저장소</a> ·
  <a href="https://github.com/JeongSeongMok/buylow/blob/main/README.md">설치·사용 가이드</a> ·
  <a href="https://github.com/JeongSeongMok/buylow/releases">릴리즈</a>
</p>

---

## 왜 buylow인가

- **코드 없는 전략 빌더** — 7종 시그널(EMA·MACD·RSI·모멘텀·볼린저밴드·가치·수급)을 AND/OR 규칙으로 조합. 프로그래밍 지식 없이 한국 주식 자동매매 전략을 만듭니다.
- **백테스트 = 라이브 동형성** — QuantConnect LEAN 엔진을 사용해 백테스트한 전략을 **수정 없이** 실거래에 투입합니다. "백테스트는 좋았는데 실전은 다른" 문제를 구조적으로 차단합니다.
- **한국 시장 특화** — KOSPI·KOSDAQ 전 종목 데이터(pykrx), 한국형 수수료·세금 모델, 외국인·기관·개인 **수급** 시그널, 한국투자증권(KIS) 연동.
- **완전 로컬** — 내 PC에서만 도는 로컬 웹 대시보드. 데이터·API 키가 외부로 전송되지 않습니다.
- **바로 실행** — Docker 한 줄로 기동(Windows·macOS·Linux). 네이티브 설치(Linux·macOS)도 지원.

## 주요 기능

| 영역 | 내용 |
|---|---|
| 전략 빌더 | 7종 시그널 + AND/OR 규칙 + 신호 보유 기간, 단일 전략 저장 |
| 백테스트 | 한국 전 종목 일봉/분봉 백테스트, 한국어 결과 요약(억·만원), 거래내역 |
| 데이터 | pykrx 일봉·펀더멘털·수급 증분 적재 + KIS 분봉, 자동 스케줄러 |
| 체결 타이밍 | 시가/종가/지정시각/TWAP/되돌림(pullback) 실행 모델 |
| 리스크 관리 | 종목별 손절·익절·트레일링 스탑 |
| 실거래(라이브) | 한국투자증권(KIS) 실전·모의 분리, 자동매매 토글, 매매 대시보드 |

## 빠른 시작

```bash
git clone https://github.com/JeongSeongMok/buylow.git
cd buylow
docker compose up
# 브라우저에서 http://127.0.0.1:8420 접속 → 설정 탭에 KIS 키 입력 → 백테스트
```

자세한 설치·설정은 **[README(한국어)](https://github.com/JeongSeongMok/buylow/blob/main/README.md)** /
**[English](https://github.com/JeongSeongMok/buylow/blob/main/README.en.md)** /
**[日本語](https://github.com/JeongSeongMok/buylow/blob/main/README.ja.md)** 를 참고하세요.

## 기술 스택

QuantConnect LEAN · Python(FastAPI · APScheduler · pythonnet) · .NET 10 · SQLite · pykrx · 한국투자증권 OpenAPI(KIS) · HTMX · Docker

---

> ⚠️ **면책**: buylow는 투자 자문이 아니며, 자동매매로 인한 모든 투자 손익의 책임은 사용자 본인에게 있습니다.
> 실거래 전 반드시 **모의투자**로 충분히 검증하세요.

[GitHub에서 소스 보기 →](https://github.com/JeongSeongMok/buylow)
