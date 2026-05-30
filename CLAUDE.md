# buylow — 한국 주식 자동 알고리즘 트레이딩 서버

> 이 문서는 프로젝트의 목표·아키텍처 결정·개발 가이드를 담은 instruction이다.
> QuantConnect **LEAN** 오픈소스를 분석해 도출한 합의 사항을 기준으로 한다.

---

## 0. 에이전트 작업 규칙 (먼저 읽을 것)

> 이 프로젝트에서 작업하는 모든 Claude 세션이 반드시 지킨다.

1. **지시·문서는 CLAUDE.md에 누적**한다. CLAUDE.md가 너무 무거워지면 주제별 인덱스 파일로 분리하고(예: `docs/dev-setup.md`, `docs/code-style.md`, `docs/roadmap.md`) CLAUDE.md에서 링크해 참고하게 한다. 분리는 *필요해질 때* 한다(미리 쪼개지 않음).
2. **`README.md`는 살아있는 최신 문서**다. 프로젝트 전체 구조·전체 파이프라인·매매 전략을 담는다. **피쳐 작업이 진행될 때마다, 또는 아키텍처가 변경될 때마다 README.md를 반드시 갱신**해 항상 최신 정보를 유지한다. (CLAUDE.md = 에이전트 작업 합의/상세 결정, README.md = 프로젝트 전체 개요)
3. **커밋은 피쳐 단위로 명확히 쪼갠다.** [Conventional Commits](https://www.conventionalcommits.org/) 규칙(`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:` …)을 따른다. **`main` 브랜치에 바로 push**한다 (PR 불필요).
4. **주석으로 "왜"를 남긴다.** 일반적인 함수는 과하게 달지 않되, **이유가 있는 로직(매매 판단 등)이나 특별한 이유가 있는 설계 결정(DLL 분리, 프로세스 구조 등)에는 항상 근거 주석**을 단다. 나중에 다른 Claude 세션/에이전트가 붙어도 의도를 명확히 이해할 수 있게 작성한다.

---

## 1. 프로젝트 목표

**KOSPI/KOSDAQ(한국 주식) 대상 개인용 자동 알고리즘 트레이딩 서버**를 구축한다.

- 증권사 연동(주문/체결/실시간시세)은 **토스증권(Toss Securities) API**를 사용한다. → **나중 단계.**
- 백테스트와 라이브를 같은 전략 코드로 돌리는 것을 핵심 가치로 삼는다 (LEAN의 *백테스트=라이브 동형성*을 그대로 활용).

---

## 2. 확정된 아키텍처 (핵심)

LEAN을 **밑바닥부터 재구현하지도, hard fork 하지도 않는다.** LEAN을 *플랫폼*으로 두고 확장한다.

> ⚠️ **2026-05-30 갱신**: 아래 다이어그램의 "LEAN 런처 = NuGet 배포본"은 **폐기**됨. 배포된 `QuantConnect.Lean.Launcher` NuGet은 net462(구형)라 net10에서 못 쓴다. 대신 **우리가 net10 thin 런처(LEAN `Program.cs` 157줄 복제)를 빌드**해 Engine NuGet을 참조한다. 즉 C# 산출물은 **2개**(thin 런처 + `MyTrading.Toss.dll`). 검증 완료 상세는 §6.

```
┌────────────────────────────────────────────────────────┐
│ buylow 오케스트레이터 (이 repo, Python/FastAPI)          │  ← 상시 가동
│   제어 API · 스케줄러 · 전략(Python) · 대시보드 · DB · 알림 │
└───────────────┬────────────────────────────────────────┘
                │ 작업마다 subprocess 실행 / 결과·로그 회수
                ▼
┌────────────────────────────────────────────────────────┐
│ LEAN 런처 프로세스 (.NET, NuGet 배포본 — 안 건드림)       │
│   ├ 전략(Algorithm)        ← Python으로 작성 (pythonnet)  │
│   └ MyTrading.Toss.dll     ← 직접 만드는 유일한 C# 산출물  │
│        · TossBrokerage : IBrokerage  (주문/체결)          │
│        · TossDataQueueHandler : IDataQueueHandler (시세)   │
│        · TossBrokerageFactory                             │
│        · Market.Add("krx") + KRW + 한국 수수료/거래세      │
└────────────────────────────────────────────────────────┘
```

### 결정 사항 요약
| 항목 | 결정 | 이유 |
|---|---|---|
| LEAN 활용 방식 | **NuGet 패키지로 참조 + 플러그인 DLL로 확장** | 검증된 코어(시간동기화·동형성·지표·체결) 재사용, fork 유지보수 부담 회피 |
| 한국화 C# 코드 | **`MyTrading.Toss.dll` 하나** (LEAN을 NuGet 참조해 빌드) | 순정 LEAN 무수정. Composer가 폴더의 *.dll 스캔 + config.json이 문자열로 지목 |
| 오케스트레이터 | **Python (FastAPI + APScheduler)** | 전략·리서치·서버 한 언어. 개인 규모에 적합 |
| 전략 작성 언어 | **Python** | LEAN이 pythonnet으로 Python 알고리즘 1급 지원 |

### 2프로세스 / 3시점 (헷갈리기 쉬운 부분)
- **프로세스 2개**: ① Python 오케스트레이터(Toss.dll 안 씀) ② LEAN 런처(.NET, Toss.dll을 **런타임에 로드해 실제 주문/시세 처리**). → Toss.dll은 "불필요"가 아니라 *다른 프로세스의 필수 플러그인*.
- **시점 3개**: ① 빌드(`dotnet build` → Toss.dll 생성, LEAN은 NuGet 참조만/재빌드 X) → ② 배포(이미 빌드된 LEAN 바이너리 옆에 Toss.dll·config·data 복사) → ③ 런타임(LEAN 프로세스가 Toss.dll 로드). *게임 본체 + MOD* 비유.

### LEAN 프로세스 수명 모델
LEAN 런처는 **"프로세스 1개 = 작업 1개"** 인 실행기다 (상시 웹서버 아님).
- **백테스트/최적화** → 작업마다 spawn → 끝나면 종료(단명). 최적화는 조합마다 다수/병렬.
- **라이브** → 전략당 장시간 상주 프로세스 1개 (전략 N개 = 프로세스 N개).
- 항상 떠 있는 건 **이 Python 오케스트레이터**이고, 그가 LEAN 프로세스를 spawn/monitor/kill/재시작한다.

---

## 3. LEAN 레퍼런스 (개발 중 참고)

로컬에 클론된 LEAN 소스: **`/Users/al03044447/IdeaProjects/Lean`** (origin: github.com/QuantConnect/Lean, C#/.NET 10)

> ⚠️ 이 경로는 **읽기전용 레퍼런스**다. buylow 빌드/배포에 포함하지 않는다. 인터페이스·구조·동작을 확인할 때만 열어본다. 실제 의존성은 NuGet 패키지(`QuantConnect.Lean.Engine`, `QuantConnect.Common`, `QuantConnect.Brokerages`, `QuantConnect.Indicators` 등).

**무슨 작업엔 어느 디렉토리를 볼지:**
| 작업 | 참고 위치 (LEAN repo 기준) |
|---|---|
| 토스 브로커/시세 어댑터 구현 | `Common/Interfaces/IBrokerage`, `IBrokerageFactory`, `Common/Interfaces/IDataQueueHandler.cs` |
| 플러그인 로딩/설정 방식 | `Common/Util/Composer.cs`(*.dll 스캔), `Launcher/config.json`(Zerodha·Samco·Tradier·IB 선례) |
| KRX 시장/시간/통화 등록 | `Common/Market.cs`(`Market.Add` 라인 275; 한국 주식거래소 미지원), `Data/market-hours/market-hours-database.json` |
| 런타임 파이프라인 이해 | `Engine/AlgorithmManager.cs`(메인 루프), `Engine/DataFeeds/Synchronizer.cs`(TimeSlice/시간동기화), `Engine/TransactionHandlers/BrokerageTransactionHandler.cs` |
| 전략 작성 패턴 | `Algorithm/QCAlgorithm.cs`, `Algorithm.Framework/{Alphas,Selection,Portfolio,Risk,Execution}/`, 예제 `Algorithm.Python/`(425개) |
| 백테스트/최적화/리포트 운영 | `Research/`(주피터)·`Optimizer/`·`Report/`·`Launcher/` |

**LEAN 전략 프레임워크 5단계** (이 흐름을 buylow 전략 설계의 기준으로): 유니버스선택 → Alpha(Insight 생성) → PortfolioConstruction(PortfolioTarget) → RiskManagement → Execution(주문). 데이터는 `Insight → PortfolioTarget → Order`로 타입이 바뀌며 흐른다.

---

## 4. 앞으로의 구현 순서 (권장)

1. **한국 시장 정의** — `Market.Add("krx")`, market-hours JSON(09:00~15:30·휴장일), KRW 정산, 종목코드(6자리)↔Symbol, 한국 수수료/거래세 FeeModel
2. **한국 과거데이터 ETL** — 토스/KRX/벤더 → LEAN 데이터 포맷(zip+csv) 변환 *(백테스트 전제조건)*
3. **백테스트 검증** — Python 전략 + 위 데이터로 LEAN 백테스트가 돌아가는지
4. **토스 라이브 연동** — `MyTrading.Toss.dll`의 `IBrokerage`/`IDataQueueHandler` 구현
5. **서버화** — 프로세스 오케스트레이션, 잡큐/스케줄링, 영속화(DB), 대시보드, 알림, 운영 안정성(재접속·포지션 정합성·킬스위치)

> 백테스트가 먼저 돌아가야 전략 검증이 되므로 1~3을 우선한다. 토스 실거래(4)는 그 이후.

---

## 5. 스택 메모

- 오케스트레이터: Python + FastAPI + APScheduler (+ subprocess/asyncio로 LEAN 프로세스 제어)
- 영속화/캐시: 추후 결정 (다른 프로젝트 선례로 MySQL/Redis 후보)
- 전략: Python (LEAN pythonnet)
- 한국화 어댑터: C# 클래스라이브러리 1개 (`MyTrading.Toss.dll`), .NET 10, LEAN NuGet 참조

---

## 6. 개발 환경 & 검증 결과 (2026-05-30 기준)

### 환경 (검증됨)
- **.NET 10 SDK `10.0.300`** → `~/.dotnet`에 설치 (sudo 불필요·시스템 미오염). **기본 PATH에 없음.** 셸에서 매번:
  ```bash
  export DOTNET_ROOT="$HOME/.dotnet"; export PATH="$HOME/.dotnet:$PATH"; export DOTNET_CLI_TELEMETRY_OPTOUT=1
  ```
  (LEAN 소스 repo 전체가 `net10.0` 타깃이라 이 버전이 맞음.)
- Python `3.12.11` + `uv 0.11.8`. Homebrew 5.x 사용 가능. LEAN Python 런타임용 `python@3.11`도 설치돼 있음.
- **git 커밋 주의**: `~/.gitconfig`의 `gpg.format`이 빈 값이라 커밋이 깨짐. 전역설정 건드리지 말고 커밋 시 `git -c gpg.format=openpgp commit ...`로 우회(서명은 `commit.gpgsign=false`라 안 함). 원격(remote) 미설정 상태 — push 전 `git remote add` 필요.
- **Context7 MCP** 추가됨(`~/.claude.json` buylow local scope). LEAN/.NET/FastAPI 문서 조회용. *jira/wiki/slack(사내)·kis-mcp(타사 KIS)·mysql/redis(영속화는 5단계)는 현재 불필요로 제외.*

### ⚠️ LEAN NuGet 버전 함정 (중요)
QuantConnect NuGet에 **두 계보**가 공존하고, semver상 큰 쪽이 **구형**이라 `versions[-1]`이 함정이다.
| 계보 | 예시 버전 | 타깃 | 상태 |
|---|---|---|---|
| **`2.5.NNNNN` (현행)** | **`2.5.17757`** | **net10.0** | ✅ **이걸 쓴다** |
| `10730.0.0` (구형) | `10730.0.0` | net462 (+R.NET·RestSharp105 등 옛 의존성) | ❌ net10 빌드 비호환, 사용 금지 |

- 핵심 패키지(`QuantConnect.Lean.Engine`·`Common`·`Brokerages`·`Indicators`·`Algorithm`·`Compression`·`Configuration`)는 모두 **`2.5.17757`**.
- `Holding` 타입은 루트 `QuantConnect` 네임스페이스(`Common/Global.cs`).

### ✅ 아키텍처 전 구간 검증 완료 (end-to-end 백테스트 성공)
C#·Python 두 알고리즘 모두 동일 결과로 완주(BasicTemplateFrameworkAlgorithm, Net Profit 1.655% / Sharpe 8.472 / 3 orders / 3,943 data points).
- ✅ **C# 어댑터**: net10 classlib가 `QuantConnect.Lean.Engine 2.5.17757` 참조 → `Brokerage` 상속 + `IDataQueueHandler` 구현 → `MyTrading.Toss.dll` 빌드.
- ✅ **실행 런처 경로 확정**: 자체 **net10 thin 런처**(LEAN `Launcher/Program.cs` 157줄을 그대로 복제, Apache 라이선스)가 Engine NuGet만 참조해 실백테스트 완주. → **§2의 "NuGet 배포본 런처를 로드" 가정은 폐기**: NuGet `QuantConnect.Lean.Launcher`(net462)는 못 씀. 대신 **우리가 thin 런처 exe를 빌드**한다 (C# 산출물이 2개: thin 런처 + `MyTrading.Toss.dll`). Docker(`lean` CLI) 불필요.
- ✅ **Composer 플러그인 로딩**: 출력폴더의 핸들러/어댑터 DLL을 이름으로 로드 — 우리 `MyTrading.Toss.dll`도 동일 경로.
- ✅ **Python 전략(pythonnet)**: Python 3.11 + pandas로 Python 알고리즘 백테스트 완주.

### 재현 레시피 (백테스트)
- **런처 패키지**(모두 `2.5.17757`): `QuantConnect.Lean.Engine`, `.Messaging`, `.Queues`, `.Api`, `.Algorithm.CSharp`, `.Research`(Python용). + `Program.cs`는 LEAN `Launcher/Program.cs` 복제.
- **config.json**: LEAN `Launcher/config.json` 기반, `data-folder`만 절대경로로. (백테스트 샘플 데이터는 LEAN repo `Data/`에 SPY 등 존재)
- **Python 실행 시 env**:
  - `PYTHONNET_PYDLL` = `…/python@3.11/…/libpython3.11.dylib` (LEAN Python 런타임은 **3.11** 사용. Homebrew `python@3.11` 설치돼 있음)
  - `PYTHONPATH` = `{pandas/numpy 깐 3.11 venv}/site-packages` : `{QuantConnect.Common NuGet}/content`(여기 `AlgorithmImports.py`) : `{알고리즘 .py 디렉토리}`
  - Python 전략은 `from AlgorithmImports import *` → 이게 다수의 `QuantConnect.*` CLR 어셈블리를 로드하므로 위 DLL들이 출력폴더에 있어야 함.
- 검증용 산출물(아직 repo 미반영): 런처 `/tmp/launcherprobe/BuylowLauncher`, Toss stub `/tmp/tossprobe/MyTrading.Toss`, 3.11 venv `/tmp/launcherprobe/leanpy`.

### 다음
- 검증물을 **repo 정식 구조로 스캐폴딩** (thin 런처 프로젝트 + `MyTrading.Toss` 프로젝트 + Python 전략/오케스트레이터 + LEAN Python 런타임 환경 정의).
- 그 후 **§4의 1단계(한국 시장 정의)** 진입.

---

*이 결정들의 상세 맥락은 Claude 메모리(`project-trading-server-goal`, `lean-as-platform-decision`, `toss-adapter-architecture`, `orchestrator-stack-python`, `lean-reference-repo`)에도 저장되어 있다.*
