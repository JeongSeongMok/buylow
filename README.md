<div align="center">

# buylow

**[QuantConnect LEAN](https://github.com/QuantConnect/Lean) 기반, 한국 주식(KOSPI/KOSDAQ) 자동 알고리즘 트레이딩.**

전략을 한 번 작성해 **백테스트**와 **라이브** 양쪽에서 그대로 실행합니다.

<sub>English / 日本語 번역은 추후 추가 예정입니다.</sub>

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
- 로컬 브라우저 대시보드로 전략 선택·백테스트·라이브 제어
- _예정:_ AI 자연어 전략 생성, 전략 스케줄링

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

**대시보드** (전략 선택 → 백테스트 실행 → 이력 보기):

```bash
export LEAN_DATA_DIR=/path/to/lean/Data      # LEAN 포맷 시세 데이터 폴더
.venv/bin/python -m orchestrator.api         # http://127.0.0.1:8420 (포트: BUYLOW_DASHBOARD_PORT)
```

**CLI 스모크 테스트** (엔진 연동만 빠르게 확인):

```bash
export LEAN_DATA_DIR=/path/to/lean/Data
./scripts/run-backtest.sh                    # 종료 코드 0이면 연동 정상
```

자세한 설정·실행은 [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md).

## 로드맵

- [x] LEAN 연동 (백테스트, C# + Python)
- [~] KRX 시장 정의 (장시간·KRW·한국 수수료/거래세 — Python 레이어 완료)
- [x] 한국 과거데이터 ETL (KRX → LEAN 포맷, pykrx/FDR; 종목별 + 유니버스 일괄 KOSPI200/KOSPI/ALL)
- [x] 수급 ETL (투자자별 순매수 — 외국인/기관/개인, KRX 로그인 필요)
- [x] 전략 카탈로그 + 대시보드 조합(레지스트리): LEAN 내장 Alpha(EMA교차·MACD·RSI·모멘텀)를 골라 결합 백테스트
- [x] 한국 특화 커스텀 Alpha: **수급 추종**(외국인 순매수) · **저PBR 가치**(PER/PBR) — 커스텀 데이터 기반
- [x] **규칙 엔진**: 조건(EMA/MACD/RSI/모멘텀)을 `(EMA AND MACD) OR RSI`처럼 자유 조합해 백테스트 (`/rules`)
- [ ] 토스증권 라이브 거래 어댑터
- [~] 오케스트레이터: 백테스트·이력(SQLite)·대시보드·백그라운드 잡·일일 스케줄러 (완료) → 알림 (예정)

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
