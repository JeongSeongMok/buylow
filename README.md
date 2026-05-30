# buylow

> 한국 주식(KOSPI/KOSDAQ) 대상 개인용 **자동 알고리즘 트레이딩 서버**.
> QuantConnect **LEAN** 엔진을 플랫폼으로 삼아, 같은 전략 코드로 **백테스트 = 라이브**를 돌리는 것을 핵심 가치로 한다.

> ⚠️ **이 README는 프로젝트의 최신 상태를 담는 살아있는 문서입니다.** 피쳐 작업·아키텍처 변경 시 항상 갱신됩니다.
> 에이전트 작업 규칙·상세 아키텍처 결정·검증 로그는 [`CLAUDE.md`](./CLAUDE.md)를 참고하세요.

---

## 1. 개요

- **대상 시장**: 한국 주식 (KOSPI/KOSDAQ).
- **증권사 연동**: 토스증권(Toss Securities) API — 주문/체결/실시간시세. *(나중 단계)*
- **엔진**: QuantConnect LEAN (.NET) — fork/재구현하지 않고 **NuGet 참조 + 플러그인으로 확장**.
- **전략 언어**: Python (LEAN의 pythonnet 1급 지원).
- **오케스트레이터**: Python (FastAPI + APScheduler).

## 2. 아키텍처

항상 떠 있는 **Python 오케스트레이터**가, 작업마다 **LEAN 프로세스**(.NET)를 spawn/monitor/kill 한다.

```
┌─────────────────────────────────────────────────────────────┐
│ buylow 오케스트레이터 (Python/FastAPI)            ← 상시 가동   │
│   제어 API · 스케줄러 · 전략 관리 · 대시보드 · DB · 알림        │
└───────────────┬─────────────────────────────────────────────┘
                │ 작업마다 subprocess spawn / 결과·로그 회수
                ▼
┌─────────────────────────────────────────────────────────────┐
│ LEAN 프로세스 (.NET 10, "프로세스 1개 = 작업 1개")             │
│   ├ thin 런처 (직접 빌드: LEAN Program.cs 복제 + Engine NuGet) │
│   ├ 전략(Algorithm)          ← Python (pythonnet)             │
│   └ MyTrading.Toss.dll       ← 직접 만드는 한국화 어댑터        │
│        · TossBrokerage : IBrokerage        (주문/체결)        │
│        · TossDataQueueHandler : IDataQueueHandler (시세)      │
│        · Market.Add("krx") + KRW + 한국 수수료/거래세          │
└─────────────────────────────────────────────────────────────┘
```

- **C# 산출물 2개**: ① thin 런처 exe(LEAN `Program.cs`를 그대로 복제, Engine NuGet만 참조 — 배포된 `QuantConnect.Lean.Launcher` NuGet은 net462라 못 씀) ② `MyTrading.Toss.dll`(한국화 어댑터).
- **LEAN은 무수정**: net10 NuGet 패키지(`2.5.17757` 계보)로 참조만 한다.
- 자세한 근거는 [`CLAUDE.md` §2](./CLAUDE.md).

## 3. 전체 파이프라인

### 백테스트 (✅ 동작 검증 완료 · repo에서 재현 가능)
```
전략(Python) + 과거데이터(LEAN 포맷)
   → thin 런처가 LEAN 엔진 구동 → TimeSlice 단위 시뮬레이션 → 통계/리포트
```
- **실행**: `scripts/run-backtest.sh` (기본 `strategies/SmokeTestAlgorithm.py`). 처음 실행 시 LEAN Python 런타임 venv(`.leanpy`)를 자동 생성.
- 검증: 우리 thin 런처 + `SmokeTestAlgorithm`(Python) end-to-end 완주(종료코드 0). 상세 레시피는 [`CLAUDE.md` §6](./CLAUDE.md).
- ⚠️ 현재 데이터는 LEAN 레퍼런스의 US 샘플(SPY)을 사용 — 한국 시장/데이터 연동은 이후 단계.

### 라이브 (🔜 예정)
```
실시간시세(TossDataQueueHandler) → 전략(Insight 생성) → 주문(TossBrokerage) → 체결 동기화
```

### LEAN 전략 프레임워크 5단계 (전략 설계 기준)
`유니버스 선택 → Alpha(Insight) → PortfolioConstruction(PortfolioTarget) → RiskManagement → Execution(Order)`

## 4. 매매 전략

> 🔜 아직 구현된 전략 없음. 전략을 추가할 때마다 이 섹션에 **전략명 / 아이디어 / 진입·청산 규칙 / 리스크 관리 / 백테스트 결과**를 정리한다.

| 전략 | 상태 | 요약 |
|---|---|---|
| — | — | (아직 없음) |

## 5. 저장소 구조

```
buylow/
├─ launcher/      # ✅ C# net10 thin 런처 (BuylowLauncher.csproj, Program.cs, config.json)
├─ strategies/    # ✅ Python 전략 (SmokeTestAlgorithm.py)
├─ scripts/       # ✅ 실행 스크립트 (run-backtest.sh)
├─ adapter/       # 🔜 C# MyTrading.Toss (Toss 어댑터 + KRX 시장정의)
├─ orchestrator/  # 🔜 Python FastAPI + APScheduler
├─ data/          # 🔜 LEAN 포맷 한국 시세 데이터
├─ runtime/       # 🔜 배포 조립
├─ CLAUDE.md      # 에이전트 작업 규칙 + 아키텍처 결정 + 검증 로그
└─ README.md      # 이 문서 (프로젝트 최신 개요)
```

## 6. 현재 상태 & 로드맵

- [x] 툴체인 검증 (.NET 10, LEAN NuGet, C#/Python 백테스트 end-to-end, 플러그인 로딩) — 2026-05-30
- [x] LEAN 연동 스모크 테스트 repo 정식화 (thin 런처 + 샘플 전략 + `run-backtest.sh`) — 2026-05-30
- [ ] 저장소 스캐폴딩 잔여 (adapter / orchestrator)
- [ ] **1. 한국 시장 정의** — `Market.Add("krx")`, market-hours(09:00~15:30·휴장일), KRW, 종목코드(6자리)↔Symbol, 한국 수수료/거래세 FeeModel
- [ ] **2. 한국 과거데이터 ETL** — KRX/벤더 → LEAN 포맷(zip+csv)
- [ ] **3. 백테스트 검증** — 한국 데이터 + Python 전략
- [ ] **4. 토스 라이브 연동** — `IBrokerage`/`IDataQueueHandler` 구현
- [ ] **5. 서버화** — 오케스트레이션·스케줄링·DB·대시보드·알림·운영 안정성

## 7. 개발 환경

- .NET 10 SDK, Python 3.12 + `python@3.11`(LEAN 런타임), `uv`. 상세·재현 레시피는 [`CLAUDE.md` §6](./CLAUDE.md).
