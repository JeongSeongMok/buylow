<div align="center">

# buylow

**[QuantConnect LEAN](https://github.com/QuantConnect/Lean) 기반, 한국 주식(KOSPI/KOSDAQ) 자동 알고리즘 트레이딩.**

전략을 한 번 작성해 **백테스트**와 **라이브** 양쪽에서 그대로 실행합니다.

[English](./README.md) · 한국어 · [日本語](./README.ja.md)

</div>

---

> ⚠️ **상태: 초기 개발 단계.** 현재 백테스트 연동은 동작하며, 한국 시장 데이터와 토스증권
> 라이브 연동은 진행 중입니다. **아직 실거래용이 아닙니다.**

## 개요

buylow는 LEAN 엔진을 플랫폼으로 사용합니다. 상시 가동되는 Python 오케스트레이터가 작업마다
LEAN(.NET) 프로세스를 실행하고, 한국화/토스 어댑터가 시장 정의와 라이브 거래를 위해 LEAN에
플러그인으로 결합됩니다. 전략은 순수 Python 파일이므로 *백테스트한 코드 그대로* 라이브에서
거래됩니다. 전체 설계는 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)를 참고하세요.

## 기능

- 매매 전략(BNF 평균회귀, 추세추종 등)을 LEAN 엔진에서 실행
- **같은** 전략 코드로 백테스트와 라이브 거래
- **사용자 본인 API 키 사용**(토스, AI) — 저장소에는 어떤 키도 포함되지 않음
- _예정:_ 전략 선택·스케줄링, AI 자연어 전략 생성, 대시보드

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

토스·AI API 키는 본인이 입력합니다. 우선순위: **환경변수**(예: `export BUYLOW_TOSS_APP_KEY=...`)
→ 로컬 `config.local.yaml` → 둘 다 없으면 **첫 실행 시 대시보드에서 입력**. 키는 로컬
`config.local.yaml`(gitignore)에 저장되며 절대 커밋되지 않습니다.

## 사용법

현재 제공: 엔진 연동을 end-to-end로 검증하는 **LEAN 백테스트 스모크 테스트**.

```bash
# LEAN 포맷 시세 데이터 폴더를 지정
export LEAN_DATA_DIR=/path/to/lean/Data
./scripts/run-backtest.sh
```

종료 코드 `0`이면 연동 정상입니다. 자세한 내용은 [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md).

## 로드맵

- [x] LEAN 연동 (백테스트, C# + Python)
- [ ] KRX 시장 정의 (장시간, KRW, 수수료/거래세)
- [ ] 한국 과거데이터 ETL (KRX → LEAN 포맷)
- [ ] 토스증권 라이브 거래 어댑터
- [ ] 오케스트레이터: 스케줄링, 영속화, 대시보드, 알림

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
