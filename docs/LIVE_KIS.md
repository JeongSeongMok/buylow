# Live trading via KIS (한국투자증권) — LEAN brokerage adapter

> ⚠️ **Real-money path.** This document describes the live-trading engine: a C# `IBrokerage` +
> `IDataQueueHandler` adapter (`adapter/MyTrading.Kis`) that lets the **same strategy `.py`** run
> live through LEAN, placing real orders on KIS. The arming switch has been **removed**: when the
> 자동매매 toggle is on (`enabled`), both real and demo transmit orders immediately. The only optional
> guard is a per-order amount cap (0 = off). End-to-end validation requires a KIS account
> (start with **모의투자/demo**). See [ARCHITECTURE.md](./ARCHITECTURE.md) for the surrounding design.

## Why this shape

The core value of buylow is **backtest = live isomorphism**: one strategy runs in both modes,
only the generated LEAN config differs. So instead of a separate Python order loop, live trading
is **LEAN live mode** + a broker adapter DLL — the layers ① daily selection (`RuleAlpha`) and
② intraday timing (`ExecutionModel`) execute unchanged; the adapter only turns LEAN orders into
KIS REST calls and KIS fills/quotes back into LEAN events.

Only KIS is wired today; Toss follows the same `IBrokerage` shape when its API opens.

## Components (`adapter/MyTrading.Kis/`)

| File | Role |
|---|---|
| `KisConstants.cs` | Endpoints, TR ids (real `T*` / demo `V*`), URLs, ORD_DVSN, krx market id 50 / KRW |
| `KisRestClient.cs` | OAuth token (mem + disk cache), `OrderCash`, `ReviseCancel`, `InquireBalance`, `InquirePsblQty`, `IsMarketOpenDay`, `ApprovalKey` |
| `KisWebSocketClient.cs` | approval-key WS: realtime price `H0STCNT0` → feed; fill notice `H0STCNI0`(real)/`H0STCNI9`(demo, AES-CBC) → order events. Pure parsers `ParseTradeBody`/`ParseFillBody` |
| `KisSymbolMapper.cs` | LEAN `Symbol`(market=krx) ↔ 6-digit code |
| `KisBrokerageModel.cs` | DefaultMarkets=krx, cash account (leverage 1), `KoreanFeeModel` (0.015% 수수료 + 0.18% 매도세 — matches `market/krx.py`), 지정가/시장가만 허용 |
| `KisBrokerage.cs` | `Brokerage` + `IDataQueueHandler`. Connect→token+WS; PlaceOrder→order-cash (**optional amount-cap check only**); Update/Cancel→rvsecncl; GetAccountHoldings/GetCashBalance→inquire-balance; fill-notice→`OnOrderEvents`; Subscribe→WS price |
| `KisBrokerageFactory.cs` | Composer entry. Reads `kis-*` `BrokerageData`, builds `KisBrokerage`, registers it as the data-queue handler |

The DLL is **not referenced by the launcher** (launcher stays unmodified). `scripts/build-adapter.sh`
builds it and copies `MyTrading.Kis.dll` next to `BuylowLauncher.dll` so LEAN's Composer can load
`KisBrokerage` by name.

## KIS API reference (TR ids)

| Purpose | Path | TR (real / demo) |
|---|---|---|
| 주식주문(현금) 매수 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0012U` / `VTTC0012U` |
| 주식주문(현금) 매도 | 〃 | `TTTC0011U` / `VTTC0011U` |
| 주식주문(정정취소) | `/uapi/.../trading/order-rvsecncl` | `TTTC0013U` / `VTTC0013U` |
| 주식잔고조회 | `/uapi/.../trading/inquire-balance` | `TTTC8434R` / `VTTC8434R` |
| 매수가능조회 | `/uapi/.../trading/inquire-psbl-order` | `TTTC8908R` / `VTTC8908R` |
| 국내휴장일조회 | `/uapi/.../quotations/chk-holiday` | `CTCA0903R` (공통) |
| 실시간 체결가 / 호가 (WS) | — | `H0STCNT0` / `H0STASP0` |
| 체결통보 (WS) | — | `H0STCNI0` / `H0STCNI9` |

Order body keys are UPPERCASE (`CANO, ACNT_PRDT_CD, PDNO, ORD_DVSN, ORD_QTY, ORD_UNPR`).
`ORD_DVSN` `00`=지정가, `01`=시장가. Account `"12345678-01"` → `CANO=12345678`, `ACNT_PRDT_CD=01`.

## LEAN live config (`live-kis` environment)

`orchestrator/lean/runner.py` `build_live_config()` generates it. Handlers:

```jsonc
"environment": "live-kis",
"environments": { "live-kis": {
  "live-mode": true,
  "live-mode-brokerage": "KisBrokerage",
  "data-queue-handler": ["KisBrokerage"],
  "setup-handler": "QuantConnect.Lean.Engine.Setup.BrokerageSetupHandler",
  "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler",
  "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed",
  "real-time-handler": "QuantConnect.Lean.Engine.RealTime.LiveTradingRealTimeHandler",
  "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BrokerageTransactionHandler",
  "history-provider": ["BrokerageHistoryProvider", "...SubscriptionDataReaderHistoryProvider"]
}}
```

Brokerage data injected at top level (read by `KisBrokerageFactory.BrokerageData`):
`kis-app-key, kis-app-secret, kis-account-no, kis-env(real|demo), kis-hts-id,
kis-max-order-amount(원), kis-token-cache`.

## Safety — start guard + optional amount cap

The **arming switch was removed** (per user decision): turning on the 자동매매 toggle trades on every
env, real included. The only safety left:

1. **Start guard** — `config.live_start_ok()` refuses to start a live run unless `enabled` is true.
   `LeanRunner.run_live()` checks this before spawning. (No real/demo distinction — `enabled` is the
   only switch.)
2. **Optional amount cap** — `KisBrokerage.PlaceOrder` rejects any single order whose value exceeds
   `kis-max-order-amount` (원, 0 = no cap). This is the only per-order brake; set one before using real.

Default is **disabled** (`enabled: false`) — the system never trades until you turn it on. ⚠️ But once
on, **real money transmits with no further confirmation**, so verify on demo first and keep `max_order_amount`
sane for real.

`live:` config fields (`orchestrator/config.py`): `enabled`, `max_order_amount` (원).
**HTS ID는 `live:`가 아니라 설정 탭의 증권사별 시크릿**(`kis_hts_id`/`kis_demo_hts_id`, app_key와 동일
관리·실전/모의 분리)으로 등록한다 — 체결통보 WS 구독에 필요하며, **없으면 `live_start_ok`가 라이브 시작을
막는다**(주문 체결이 LEAN에 반영되지 않아 포지션/리스크 추적이 어긋나므로 필수). **`env`(demo|real)는 저장하지
않고 선택한 증권사로 도출**(`config.broker_env`: `kis`→real, `kis_demo`→demo). 실전/모의 전환은 설정 탭의
**증권사 선택**(KIS 실전 / KIS 모의투자)으로 하고, 키·계좌·HTS ID도 증권사별로 분리 저장된다
(`BROKER_SECRET_SPECS["kis"|"kis_demo"]`). 데이터(시세·분봉)는 env와 무관하게 항상 실전 도메인이다.

## Build & run

```bash
# 1) 어댑터 빌드 + 런처 출력폴더로 복사 (런처를 한 번이라도 빌드해 출력폴더가 있어야 함)
scripts/build-adapter.sh

# 2) C# 어댑터 단위테스트 (프레임 파싱·상수 분기)
DOTNET_ROOT=$HOME/.dotnet dotnet test adapter/MyTrading.Kis.Tests

# 3) 파이썬 라이브 설정/콘피그 생성 테스트
.venv/bin/pytest tests/test_live.py
```

`build-adapter.sh` is bash-only. On **Windows (PowerShell)** the script step has no shell equivalent,
so run what it does directly — build the launcher (so its output folder exists), build the adapter,
then copy the DLL next to the launcher:

```powershell
dotnet build launcher\BuylowLauncher.csproj -c Release             # 런처(출력폴더 생성)
dotnet build adapter\MyTrading.Kis\MyTrading.Kis.csproj -c Release # 어댑터
Copy-Item adapter\MyTrading.Kis\bin\Release\net10.0\MyTrading.Kis.dll launcher\bin\Release\net10.0\
```

## 모의투자(demo) 수동 검증 절차 (e2e)

자동 e2e는 실계좌가 필요해 CI에서 돌리지 않는다. KIS **모의투자** 계좌로 수동 점검:

1. 설정 탭에서 **증권사 = KIS 모의투자(`kis_demo`)** 를 고르고, 모의 App Key/Secret + 모의 계좌번호를
   넣는다(env는 증권사 선택으로 자동 demo).
2. `config.local.yaml`:
   ```yaml
   broker: kis_demo       # 증권사 선택이 곧 env=demo (모의 서버)
   live:
     enabled: true        # 켜면 바로 매매(무장 없음)
     max_order_amount: 1000000   # 0이면 한도 없음; 안전하게 한도를 둔다
   secrets:
     kis_demo_app_key: "..."
     kis_demo_app_secret: "..."
     kis_demo_account_no: "50012345-01"
     kis_demo_hts_id: "<모의 HTS ID>"   # 체결통보 구독 필수(없으면 라이브 시작 거부)
   ```
3. `scripts/build-adapter.sh`로 DLL 배치.
4. 분봉 데이터가 있는 소수 종목으로 전략(`resolution: minute`)을 저장하고, 라이브를 기동
   (`LeanRunner().run_live(req)` — 대시보드 매매 탭 연동은 후속 작업).
5. 로그(`runs/live-*/run.log`)에서 토큰 발급 → WS 연결 → 주문 전송(ODNO) → 체결통보 → LEAN
   OrderEvent 순서를 확인한다. 잔고/예수금이 LEAN 포트폴리오에 반영되는지 확인.

## 한계 / 후속

- **GetOpenOrders**는 빈 목록(새 세션은 미체결 동기화 안 함) — 재시작 시 기존 미체결 복구는 미구현.
- **체결통보 HTS ID는 필수**(설정 탭 시크릿) — 없으면 `live_start_ok`가 라이브 시작을 막는다(실시간
  체결확인 불가 → 포지션/리스크 추적 어긋남 방지). REST 체결폴링 폴백은 미구현.
- **수수료**는 OrderEvent에 0으로 보고하고 잔고로 정산 — 정밀 체결수수료 반영은 후속.
- **킬 스위치/프로세스 감독**(장시간 라이브 프로세스 모니터·재시작)은 JobManager 확장으로 후속.
- **매매 탭 자동매매 가동(구현됨)**: 매매 탭에서 **대상종목(라이브 유니버스)**을 인덱스·그룹·검색으로 골라
  저장하고, **자동매매 토글 ON**(`/trade/toggle`)이 가드(enabled·전략·유니버스·어댑터 DLL; 무장 없음) 통과 시
  `LiveProcessManager.start`(`orchestrator/live_runner.py`)로 LEAN 라이브 프로세스를 spawn,
  **OFF**면 `stop()`으로 종료(킬 스위치). `run_live(proc_sink=...)`가 Popen 핸들을 매니저에 넘긴다.
  해상도는 저장 전략의 resolution(분봉=1분봉마다). 남은 것: 라이브 프로세스 헬스/재시작 감독.
- **Toss**는 동일 `IBrokerage` 형태로 추가; 대시보드엔 KIS∩Toss 교집합 기능만 노출.
- ⛔ 실전(real) 실주문은 **실계좌 검증 전까지 토글을 켜지 말 것**(무장 게이트가 없어 켜면 바로 나간다).
