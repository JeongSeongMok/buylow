# Live trading via KIS (한국투자증권) — LEAN brokerage adapter

> ⚠️ **Real-money path.** This document describes the live-trading engine: a C# `IBrokerage` +
> `IDataQueueHandler` adapter (`adapter/MyTrading.Kis`) that lets the **same strategy `.py`** run
> live through LEAN, placing real orders on KIS. Real order submission is gated behind an
> **arming** switch and a per-order amount cap. End-to-end validation requires a KIS account
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
| `KisBrokerage.cs` | `Brokerage` + `IDataQueueHandler`. Connect→token+WS; PlaceOrder→order-cash (**arming gate**); Update/Cancel→rvsecncl; GetAccountHoldings/GetCashBalance→inquire-balance; fill-notice→`OnOrderEvents`; Subscribe→WS price |
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
`kis-app-key, kis-app-secret, kis-account-no, kis-env(real|demo), kis-hts-id, kis-armed(true|false),
kis-max-order-amount(원), kis-token-cache`.

## Safety — arming gate

Real money demands a hard brake. Two layers, configured in `config.local.yaml` `live:` (dashboard
later) and enforced in **both** Python and C#:

1. **Orchestrator gate** — `config.live_arming_ok()` refuses to start a live run unless
   `enabled` is true, and for `env=real` also requires `armed=true`. `LeanRunner.run_live()` checks
   this before spawning.
2. **Brokerage gate** — `KisBrokerage.PlaceOrder` refuses to transmit unless `kis-armed=true`
   (otherwise the order is marked `Invalid` with a "미무장 드라이런" message), and rejects any single
   order whose value exceeds `kis-max-order-amount` (원, 0 = no cap — set one for real).

Defaults are **disabled, unarmed, demo** — the system never trades by accident.

`live:` config fields (`orchestrator/config.py`): `enabled`, `armed`, `env` (`demo`|`real`),
`max_order_amount` (원), `hts_id` (체결통보 WS 구독용 HTS 아이디; 없으면 실시간 체결확인 생략).

## Build & run

```bash
# 1) 어댑터 빌드 + 런처 출력폴더로 복사 (런처를 한 번이라도 빌드해 출력폴더가 있어야 함)
scripts/build-adapter.sh

# 2) C# 어댑터 단위테스트 (프레임 파싱·상수 분기)
DOTNET_ROOT=$HOME/.dotnet dotnet test adapter/MyTrading.Kis.Tests

# 3) 파이썬 라이브 설정/콘피그 생성 테스트
.venv/bin/pytest tests/test_live.py
```

## 모의투자(demo) 수동 검증 절차 (e2e)

자동 e2e는 실계좌가 필요해 CI에서 돌리지 않는다. KIS **모의투자** 계좌로 수동 점검:

1. 설정에 KIS App Key/Secret + 모의 계좌번호를 넣는다(설정 탭/`config.local.yaml`).
2. `config.local.yaml`:
   ```yaml
   live:
     enabled: true
     armed: true          # 모의는 안전하지만 게이트 동작 확인용으로 켜본다
     env: demo
     max_order_amount: 1000000
     hts_id: "<모의 HTS ID>"   # 체결통보 받으려면 필요
   ```
3. `scripts/build-adapter.sh`로 DLL 배치.
4. 분봉 데이터가 있는 소수 종목으로 전략(`resolution: minute`)을 저장하고, 라이브를 기동
   (`LeanRunner().run_live(req)` — 대시보드 매매 탭 연동은 후속 작업).
5. 로그(`runs/live-*/run.log`)에서 토큰 발급 → WS 연결 → 주문 전송(ODNO) → 체결통보 → LEAN
   OrderEvent 순서를 확인한다. 잔고/예수금이 LEAN 포트폴리오에 반영되는지 확인.

## 한계 / 후속

- **GetOpenOrders**는 빈 목록(새 세션은 미체결 동기화 안 함) — 재시작 시 기존 미체결 복구는 미구현.
- **체결통보 HTS ID 미설정 시** 실시간 체결확인 불가(주문은 Submitted까지). 체결은 잔고로만 반영.
- **수수료**는 OrderEvent에 0으로 보고하고 잔고로 정산 — 정밀 체결수수료 반영은 후속.
- **킬 스위치/프로세스 감독**(장시간 라이브 프로세스 모니터·재시작)은 JobManager 확장으로 후속.
- **매매 탭 대시보드**(계좌/잔고/장상태/자동매매 on·off)는 다음 단계(Stage 1)에서 이 엔진 위에 얹는다.
- **Toss**는 동일 `IBrokerage` 형태로 추가; 대시보드엔 KIS∩Toss 교집합 기능만 노출.
- ⛔ 실전(real) 실주문은 **실계좌 검증 전까지 무장하지 말 것**.
