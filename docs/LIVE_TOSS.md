# Live trading via Toss (토스증권) — LEAN brokerage adapter

> ⚠️ **Real-money path.** This document describes the Toss live-trading engine: a C# `IBrokerage` +
> `IDataQueueHandler` adapter (`adapter/MyTrading.Toss`) that lets the **same strategy `.py`** run
> live through LEAN, placing real orders on Toss Securities. As with KIS, there is no arming switch —
> when the 자동매매 toggle is on (`enabled`), orders transmit immediately. The only optional guard is a
> per-order amount cap (0 = off). **Toss has no demo/모의 server** (real only), so end-to-end validation
> must be done on a real account with small sizes. See [LIVE_KIS.md](./LIVE_KIS.md) for the KIS sibling
> and [ARCHITECTURE.md](./ARCHITECTURE.md) for the surrounding design.

## Why this shape

Same as KIS — buylow's core value is **backtest = live isomorphism**: one strategy runs in both
modes, only the generated LEAN config differs. Live trading is **LEAN live mode** + a broker adapter
DLL; the layers ① daily selection (`RuleAlpha`) and ② intraday timing (`ExecutionModel`) execute
unchanged. The adapter turns LEAN orders into Toss REST calls and Toss order/price state back into
LEAN events.

## How Toss differs from KIS (the important part)

Toss Securities Open API has **no realtime WebSocket** for fills or quotes. So the adapter is
**polling-based** where KIS is push-based:

| Concern | KIS | Toss |
|---|---|---|
| Auth | appkey/appsecret (JSON) | OAuth2 client-credentials (`client_id`/`client_secret`, form) |
| Account scope | `CANO`/`ACNT_PRDT_CD` in body | `X-Tossinvest-Account: {accountSeq}` header (auto-resolved via `getAccounts`) |
| Demo/모의 | separate keys + server | **none** — real only |
| Fills | WebSocket 체결통보 (`H0STCNI0`) | **poll `getOrder(orderId)`** until terminal → `OnOrderEvent` |
| Realtime quotes | WebSocket `H0STCNT0` | **poll `getPrices(symbols)`** (≤200/call) → feed |
| HTS ID | required (체결통보 구독) | **not needed** (no fill WebSocket) |

Because fills come from polling, **no HTS ID gate** applies to Toss — `live_start_ok` only requires
`enabled` + `client_id`/`client_secret`.

## Components (`adapter/MyTrading.Toss/`)

| File | Role |
|---|---|
| `TossConstants.cs` | Base URL, endpoint paths (`/oauth2/token`, `/api/v1/{accounts,holdings,buying-power,orders,prices,market-calendar/KR}`), krx market id 50 / KRW |
| `TossRestClient.cs` | OAuth2 token (mem + disk cache), `AccountSeq` (resolve+cache), `GetBalance`, `GetBuyingPower`, `IsMarketOpenDay`, `GetPrices`, `CreateOrder`/`ModifyOrder`/`CancelOrder` (paced + retried), `GetOrder` (fill polling) |
| `TossSymbolMapper.cs` | LEAN `Symbol`(market=krx) ↔ 6-digit code |
| `TossBrokerageModel.cs` | DefaultMarkets=krx, cash account (leverage 1), `KoreanFeeModel` (0.015% 수수료 + 0.18% 매도세 — matches `market/krx.py`), 지정가/시장가만 허용 |
| `TossBrokerage.cs` | `Brokerage` + `IDataQueueHandler`. Connect→token+accountSeq+2 poller threads; PlaceOrder→createOrder (clientOrderId idempotency, **optional amount-cap only**); Update/Cancel→modify/cancel; GetAccountHoldings/GetCashBalance→holdings/buying-power; **fill poller** getOrder→`OnOrderEvent`; **price poller** getPrices→feed |
| `TossBrokerageFactory.cs` | Composer entry. Reads `toss-*` `BrokerageData`, builds `TossBrokerage`, registers it as the data-queue handler |

The DLL is **not referenced by the launcher** (launcher stays unmodified). `scripts/build-adapter.sh`
builds it and copies `MyTrading.Toss.dll` next to `BuylowLauncher.dll` so LEAN's Composer can load
`TossBrokerage` by name. (`scripts/build-adapter.sh toss` builds only Toss.)

## Toss API reference

Base URL `https://openapi.tossinvest.com`. All calls (except the token endpoint) carry
`Authorization: Bearer {token}`; account-scoped calls additionally carry `X-Tossinvest-Account`.

| Purpose | Method · Path |
|---|---|
| 토큰 발급 (OAuth2) | `POST /oauth2/token` (form: grant_type/client_id/client_secret) |
| 계좌 목록 (accountSeq) | `GET /api/v1/accounts` |
| 보유 주식 | `GET /api/v1/holdings` |
| 매수가능금액 | `GET /api/v1/buying-power?currency=KRW` |
| 주문 생성 | `POST /api/v1/orders` (symbol/side/orderType/quantity[/price]/clientOrderId) |
| 주문 정정 / 취소 | `POST /api/v1/orders/{id}/modify` · `/cancel` |
| 주문 상세 (체결 폴링) | `GET /api/v1/orders/{id}` |
| 현재가 (시세 폴링) | `GET /api/v1/prices?symbols=005930,000660` |
| 국내 장 운영 | `GET /api/v1/market-calendar/KR?date=YYYY-MM-DD` |

Responses use a BFF envelope `{"result": ...}` (the token endpoint uses the OAuth2 standard shape).
Order amounts/quantities are strings. `orderType` `LIMIT`/`MARKET`; `side` `BUY`/`SELL`.

## LEAN live config (`live-toss` environment)

`orchestrator/lean/runner.py` `build_toss_live_config()` generates it. Handlers are identical to
`live-kis` except the brokerage name:

```jsonc
"environment": "live-toss",
"environments": { "live-toss": {
  "live-mode": true,
  "live-mode-brokerage": "TossBrokerage",
  "data-queue-handler": ["TossBrokerage"],
  "setup-handler": "QuantConnect.Lean.Engine.Setup.BrokerageSetupHandler",
  "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler",
  "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed",
  "real-time-handler": "QuantConnect.Lean.Engine.RealTime.LiveTradingRealTimeHandler",
  "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BrokerageTransactionHandler",
  "history-provider": ["BrokerageHistoryProvider", "...SubscriptionDataReaderHistoryProvider"]
}}
```

Brokerage data injected at top level (read by `TossBrokerageFactory.BrokerageData`):
`toss-client-id, toss-client-secret, toss-max-order-amount(원), toss-token-cache`.
(`runner.run_live` picks the adapter/env per `config.get_broker()` via `LIVE_ADAPTERS`.)

## Safety — start guard + optional amount cap

1. **Start guard** — `config.live_start_ok()` refuses to start unless `enabled` is true **and**
   `toss_client_id`/`toss_client_secret` are set. (No HTS ID requirement — Toss confirms fills by
   polling, not a WebSocket subscription.)
2. **Optional amount cap** — `TossBrokerage.PlaceOrder` rejects any single order whose value exceeds
   `toss-max-order-amount` (원, 0 = no cap).

Default is **disabled** (`enabled: false`). ⚠️ Toss has **no demo server**, so the first real
end-to-end test is on a real account — set a small `max_order_amount` and use a tiny universe.

`live:` config fields (`orchestrator/config.py`): `enabled`, `max_order_amount` (원). Broker selection
(`broker: toss`) and the OAuth2 keys live in the 설정 탭 secrets (`toss_client_id`/`toss_client_secret`).

## Build & run

```bash
# 1) 어댑터 빌드 + 런처 출력폴더로 복사 (런처를 한 번이라도 빌드해 출력폴더가 있어야 함)
scripts/build-adapter.sh toss      # (인자 없이 실행하면 KIS·토스 둘 다)

# 2) C# 어댑터 단위테스트 (응답 파싱·주문 상태 분류)
DOTNET_ROOT=$HOME/.dotnet dotnet test adapter/MyTrading.Toss.Tests

# 3) 파이썬 클라이언트/브로커/라이브 설정 테스트
.venv/bin/pytest tests/test_toss.py
```

## 실전 수동 검증 절차 (e2e)

토스는 모의 서버가 없어 실계좌로만 검증할 수 있다. **소액·소수 종목**으로 점검:

1. 설정 탭에서 **증권사 = 토스증권(`toss`)** 을 고르고 **Client ID / Client Secret** 을 입력한다
   (계좌는 자동 해석 — 입력 불필요). '연동 테스트'로 토큰 발급 + 계좌 조회를 확인한다.
2. `config.local.yaml`:
   ```yaml
   broker: toss
   live:
     enabled: true             # 켜면 바로 매매(무장 없음)
     max_order_amount: 100000  # 0이면 한도 없음; 실전이므로 작게 둔다
   secrets:
     toss_client_id: "..."
     toss_client_secret: "..."
   ```
3. `scripts/build-adapter.sh toss`로 DLL 배치.
4. 소수 종목으로 전략을 저장하고 매매 탭에서 대상종목(유니버스)을 고른 뒤 자동매매를 켠다.
5. 로그(`runs/live-*/run.log`)에서 토큰 발급 → accountSeq 해석 → 주문 전송(orderId) → 폴러의 체결
   확인 → LEAN OrderEvent 순서를 확인한다. 잔고/예수금이 LEAN 포트폴리오에 반영되는지 확인.

## 한계 / 후속

- **체결/시세가 폴링 기반**이라 KIS의 WebSocket보다 지연이 있다(체결 ~1.5s, 시세 ~2s 주기). 분봉
  타이밍에는 충분하나 초 단위 정밀 체결에는 KIS가 유리하다.
- **GetOpenOrders**는 빈 목록(새 세션은 미체결 동기화 안 함) — 재시작 시 기존 미체결 복구는 미구현.
  보유 포지션은 `getHoldings`로 실측되어 RuleAlpha가 델타만 거래하므로 중복 매수는 없다.
- **수수료**는 전량 체결(FILLED) 시 `getOrder`의 commission+tax를 1회 반영(부분체결 구간은 0). 부분
  체결의 평균단가는 누적 평균을 쓰는 근사.
- **종료(CLOSED) 주문 목록 조회 미지원**(Toss API `getOrders`는 OPEN만) → 매매 탭의 '매매 내역'은
  토스에선 buylow 자체 거래로그(TradeStore)로 폴백한다(KIS는 체결조회로 실거래 표시).
- **주문 안정성(구현됨)**: `TossRestClient.SendOrder`가 주문을 **최소간격 페이싱(250ms·≤4건/초) +
  429/일시적 전송오류 백오프 재시도(최대 4회)**로 감싸고, 실패해도 예외 없이 `Ok=false`만 반환한다
  (주문 1건 실패가 라이브 전체를 종료시키지 않게 — `TossBrokerage`의 catch는 모두 Warning).
- **프로세스 감독/재개**는 KIS와 공통(`LiveProcessManager` — 백오프 재시작, 부팅 시 재개, 종료 시 kill).
- ⛔ 토스는 실전 단일이라 **실계좌 검증 전까지 토글을 켜지 말 것**(켜면 바로 실주문이 나간다).
