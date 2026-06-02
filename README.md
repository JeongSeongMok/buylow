<div align="center">

# buylow

**[QuantConnect LEAN](https://github.com/QuantConnect/Lean) 기반, 한국 주식(KOSPI/KOSDAQ) 자동 알고리즘 트레이딩.**

전략을 한 번 작성해 **백테스트**와 **라이브** 양쪽에서 그대로 실행합니다.

<sub>English / 日本語 번역은 추후 추가 예정입니다.</sub>

</div>

---

> ⚠️ **상태: 개발 중.** 데이터 ETL · 전략(규칙 엔진) · 백테스트 · 리스크 관리까지 동작합니다.
> **토스증권 라이브 거래는 토스 API 오픈 대기 중** — 아직 실거래용이 아닙니다.

## 개요

buylow는 LEAN 엔진을 플랫폼으로 사용합니다. 상시 가동되는 Python 오케스트레이터가 작업마다
LEAN(.NET) 프로세스를 띄워 전략을 백테스트/라이브로 실행합니다. 한국 시장 정의·수수료·전략은
Python으로, 토스 실시간·주문(라이브)은 별도 플러그인으로 결합됩니다. 전략 정의는 그대로
백테스트=라이브에 쓰입니다(LEAN 동형성). 전체 설계는 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).

## 기능

- **데이터 ETL**: KRX 가격(OHLCV)·수급(투자자별 순매수)·펀더멘털(PER/PBR)을 LEAN 포맷으로 적재. 대시보드에선 **버튼 하나로 한국시장 전체 종목**(KOSPI·KOSDAQ)의 가격·수급을 일괄 적재(덮어쓰기)
- **규칙 엔진**: EMA·MACD·RSI·모멘텀·볼린저밴드·저평가(가치)·수급 추종 등 조건을 `(EMA AND MACD) OR RSI`처럼 자유 조합 — 대시보드에선 **조건 그룹 빌더**(그룹 안=AND, 그룹끼리=OR)로 체크만 하면 됨. 한국 특화 Alpha(수급 추종·저PBR)
- **유니버스 선별**: 적재된 전 종목을 자동 스캔
- **백테스트**: 백그라운드 실행 + 실시간 로그/상태, 결과·이력 저장(SQLite)
- **리스크 관리(전역)**: 종목별 손절·익절·트레일링 스탑
- **로컬 대시보드** 탭: 전략 설정(조건식 + 리스크) · 백테스트(기간·유니버스 정해 실행 + 결과) · 데이터(적재 현황) · 설정(키 + 전체 데이터 적재) · 작업(백그라운드 진행)
- **BYO 키**: 토스·KRX 키는 사용자 로컬에만 (저장소엔 없음)
- _예정:_ 토스 라이브 거래(API 오픈 대기)

## 파이프라인

```
ETL(가격·수급·펀더멘털) → ./data
        │
        ▼  (LEAN 5단계 · 매 거래일)
유니버스 선별 → 조건/Alpha 판단(UP/DOWN) → 포트폴리오 비중 → 리스크(손절·익절) → 주문
        │
        ▼
백테스트(지금) = 과거 데이터 재생   /   라이브(예정) = 토스 실시간·주문
```
- 같은 전략 정의가 백테스트·라이브에서 동일하게 동작.
- **매수 = 전략(조건/Alpha)**, **매도 = 전략 신호 변화 + 리스크(손익)** 두 축.

## 전제조건

- [.NET 10 SDK](https://dotnet.microsoft.com/download)
- [Python 3.11](https://www.python.org/) 및 [uv](https://github.com/astral-sh/uv)
- git

## 설치

```bash
git clone https://github.com/JeongSeongMok/buylow.git
cd buylow
# 설정 스크립트: 예정
```

## 설정

키·경로는 우선순위 **환경변수 → `config.local.yaml` → 기본값**으로 해석됩니다.
`config.example.yaml`을 `config.local.yaml`로 복사해 채우거나, 대시보드 `/settings`에서 입력하세요.
`config.local.yaml`은 gitignore되어 절대 커밋되지 않습니다.

```yaml
# config.local.yaml (예)
data_folder: ./data        # 설정하면 매번 LEAN_DATA_DIR export 불필요
dashboard_port: 8420
secrets:
  krx_id: ""               # pykrx 펀더멘털(PER/PBR)용 — data.krx.co.kr 무료 가입
  krx_pw: ""
```

## 사용법

```bash
# (최초 1회) 의존성 설치
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
# 대시보드 실행 → http://127.0.0.1:8420
.venv/bin/python -m orchestrator.api
```

대시보드 흐름(접속 시 **전략 설정** 탭으로 랜딩):
1. **설정** — (필요 시) KRX 키 입력 → **[데이터 최신화]** 버튼 *(최초엔 최근 5년치, 이후엔 마지막 적재일~오늘 증분만. 백테스트엔 데이터 필수)*
2. **전략 설정** — 조건식 `(EMA AND MACD) OR RSI` + 리스크(손절·익절) 저장
3. **백테스트** — 기간·유니버스만 정해 실행 → 상태·로그·결과 확인 (진행은 **작업** 탭, 적재 현황은 **데이터** 탭)

CLI로도 가능:
```bash
LEAN_DATA_DIR=/path/to/data .venv/bin/python -m etl.krx --ticker 005930 --from 2023-01-01 --to 2023-12-31
LEAN_DATA_DIR=/path/to/data .venv/bin/python -m orchestrator.lean --strategy strategies/SmokeTestAlgorithm.py
```
`config.local.yaml`에 `data_folder`를 넣으면 `LEAN_DATA_DIR` export가 불필요합니다. 자세한 건 [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md).

## 로드맵

**완료**
- [x] LEAN 연동 (NuGet 참조 + thin 런처, C#·Python 백테스트 end-to-end)
- [x] KRX 시장 정의 (krx 시장·KRW·한국 수수료 + 한국 시간대·무위험금리 보정 → KRX 통계 정상)
- [x] 데이터 ETL — 가격(OHLCV)·수급·펀더멘털(PER/PBR)
- [x] **데이터 최신화** — 버튼/스케줄러가 동일 로직으로 마지막 적재일~오늘 증분(전 종목), 최신 날짜 표시
- [x] 오케스트레이터 — LEAN Runner · 영속화(SQLite) · Control API · 대시보드
- [x] 백테스트 백그라운드 잡 + 실시간 로그/**진행률** 표시
- [x] **규칙 엔진** — 조건 그룹 빌더(그룹 안 AND·그룹끼리 OR)로 자유 조합, 단일 전략 영속화
- [x] 시그널 — EMA·MACD·RSI·모멘텀·**볼린저밴드(평균회귀+돌파전환)**·**저평가(가치: 저PER·저PBR + 파생 ROE)**·**수급 추종(외국인/기관/개인 선택, N일 누적 순매수)**
- [x] 유니버스 — 적재 전 종목 스캔 + **인덱스(KOSPI200/KOSDAQ150) 일괄추가**
- [x] 포트폴리오 — 동시 보유 종목 상한(유동성 상위) → 과분산 시 매매 가능
- [x] 리스크 관리(전역) — 종목별 손절·익절·트레일링
- [x] 백테스트 결과 — 한국어 친화 요약(억/만원 표기)
- [x] 설정·시크릿 — env→config.local.yaml→대시보드

**남음 / 예정**
- [ ] **토스증권 라이브** (`TossBrokerage`·`TossDataQueueHandler`) — ⛔ **토스 API 오픈 게이트**
- [ ] 라이브 실행 엔진(주기적 스케줄링) — 토스 의존
- [ ] 분봉 ETL → 단타·변동성 돌파 전략
- [ ] 파라미터 최적화(스윕) · 워크포워드
- [ ] OpenDART 깊은 재무 · 뉴스/센티먼트 데이터
- [ ] 유니버스 기준 필터(시총 상위·저PBR 등 pre-filter)
- [ ] 커스텀 리스크(ATR/변동성/시간) · PCM 선택(InsightWeighting 등)
- [ ] 결과 상세(equity 차트) · 알림 · 전략 저장(named)
- [ ] 크로스플랫폼 패키징 · 라이선스 결정

## 문서

- [아키텍처](./docs/ARCHITECTURE.md) — 시스템 설계와 근거
- [개발](./docs/DEVELOPMENT.md) — 설정·빌드·실행
- [에이전트 가이드](./CLAUDE.md) — AI 보조 개발 규약

## 면책 조항

이 소프트웨어는 교육 목적으로 제공됩니다. 자동 매매는 상당한 금융 리스크를 수반하며,
**사용에 따른 책임은 전적으로 사용자에게 있습니다.** 제작자는 어떠한 금전적 손실에도 책임지지
않습니다. 사용 시 증권사 API 약관 및 관련 법규를 반드시 준수하세요.

## 라이선스

미정(TBD).
