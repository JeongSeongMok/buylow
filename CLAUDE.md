# CLAUDE.md — Agent guide for buylow

buylow is a personal automated algorithmic trading toolkit for Korean equities
(KOSPI/KOSDAQ), built on the QuantConnect **LEAN** engine (referenced via NuGet and
extended with plugins — **never forked or modified**). Core value: the same strategy code
runs in backtest and live.

## 0. Agent working rules (read first)

Every Claude session working in this repo follows these:

1. **Accumulate instructions and decisions in this file.** When it gets heavy, split topics
   into `docs/` files and link them from here (don't pre-split).
2. **`README.md` is the user-facing, always-current doc, written in Korean.**
   Update it whenever a feature lands or the architecture changes. English/Japanese
   translations may be added later; until then maintain only `README.md` (don't create
   multiple language versions). (CLAUDE.md = working agreement + decisions; README = user overview.)
3. **Commit per feature**, using [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`). **Push directly to `main`** (no PR).
   **Always commit as `JeongSeongMok <tjdahr25@naver.com>`** — verify `git config user.name/user.email`
   before committing and set them locally if they differ (e.g. a corp identity like `retipuj`).
   (Note: if `git` fails with `invalid value for 'gpg.format': ''`, remove the empty `format =`
   line under `[gpg]` in `~/.gitconfig` — see docs/DEVELOPMENT.md.)
4. **Comment the "why."** Keep ordinary functions uncommented, but always explain the
   rationale for non-trivial logic (trading decisions) and special design choices (DLL split,
   process model) so a future session understands the intent.
5. **Public, open-source repo — language & hygiene.** Instruction/dev docs (`CLAUDE.md`,
   `docs/`) are in **English** (easier for agents); **code comments are in Korean** (the
   developer reads them); `README.md` is **Korean** for now. Keep **no secrets** in the repo
   (BYO keys in a gitignored local config) and **no machine-specific absolute paths** in
   committed files (use env vars / settings).
6. **Add tests with every implementation.** When you implement a feature, add or extend tests
   for it (pytest under `tests/`). Prefer fast unit tests for logic; mark slow/end-to-end tests
   that need the full LEAN toolchain (.NET + data) as `@pytest.mark.integration` so the default
   `pytest` run stays fast. Run the tests and confirm they pass before committing.

## 1. Key decisions

| Topic | Decision |
|---|---|
| LEAN usage | Reference via NuGet + extend with a plugin DLL (no fork) |
| Process model | 2 processes: always-on Python orchestrator + per-job LEAN process (.NET) |
| LEAN lifetime | "one process = one job"; orchestrator spawns/monitors/kills |
| Orchestrator | Python (FastAPI + APScheduler) |
| Strategy language | Python (pythonnet) |
| Brokerage model | User picks broker in dashboard: **kis(실전) / kis_demo(모의투자) / toss**(API 대기). KIS 실전·모의는 앱키·서버가 완전 분리돼 **별도 증권사로 따로 관리**(같은 KisClient/KisBroker 로직, env만 다름; `config.broker_env`). 매매(잔고/주문)는 증권사 env(kis_demo→demo 서버), **데이터(시세·분봉)는 항상 실전 도메인**(계좌 불필요). historical daily는 키리스 pykrx |
| Two-layer strategy | ① daily selection (alpha, once/day) + ② intraday timing (LEAN `ExecutionModel` on minute bars). Same code in backtest & live. Timing logic is pure (`orchestrator/execution.py`) + thin adapter (`strategies/intraday_execution.py`) — mirrors `rules.py`/`RuleAlpha` |
| C# artifacts | **Two**: a thin net10 launcher (vendored LEAN `Program.cs`) + a broker adapter DLL. `MyTrading.Kis` is **built** (`adapter/MyTrading.Kis`, gated behind arming); `MyTrading.Toss` follows when Toss API opens |
| LEAN NuGet version | `2.5.17757` lineage (net10); **never** `10730.x` (net462) — see DEVELOPMENT.md |
| Orchestrator ↔ LEAN | Filesystem (config in / results out) + process control |
| Persistence | SQLite (orchestrator-owned, WAL) for state + disk files (`runs/<id>/`) for blobs; no DB server |
| Control surface | Local browser dashboard via FastAPI on `127.0.0.1:<port>` (default 8420, configurable); HTMX+Jinja; SSE |
| Config & secrets | `config.local.yaml` (gitignored); secrets resolved env var → disk → dashboard prompt |
| Distribution | Open source, clone-and-run; BYO API keys; no installer |

## 2. Where to look

- **[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)** — system design, LEAN's role, the seams we build, distribution model, data flow, roadmap
- **[docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md)** — environment setup, build/run, the NuGet version trap, smoke test, validation log
- **[README.md](./README.md)** — user-facing overview (install/configure/run)

## 3. Current status

**Essentially complete except live trading.** The whole backtest path — data, strategy, backtest, results — works end to end; only Toss live is gated.

- LEAN integration (NuGet + thin launcher, C#/Python e2e); KRX-correct stats (Asia/Seoul TZ + constant risk-free rate)
- Data — one '데이터 최신화' path (button + daily scheduler): whole-market incremental per data type (price by-date, fundamentals by-date cross-section, flow per-ticker), 5y backfill when empty; code→name map (`etl.names`); dashboard shows latest loaded date, search, per-ticker detail with date filter
- Orchestrator — LEAN Runner, SQLite persistence, FastAPI Control API; dashboard tabs (전략 설정 / 백테스트 / 데이터 / 설정 / 작업중), landing = 전략 설정; 2-column layouts, no-wrap tables, scroll
- Background-job backtests with live log + progress %; run history; Korean result summary (억/만원) + **trade history** (date/name/side/qty/amount/reason; risk tag > signal reason)
- **Rule engine** — condition-group builder (AND within group, OR across groups); single persisted strategy; signal-hold period
- **Signals (7)** — EMA, MACD, RSI, momentum, Bollinger (mean-reversion + breakout switch), value (저PER/저PBR + derived ROE), flow (수급: foreign/inst/individual selectable, N-day cumulative)
- Universe — scan all loaded + index bulk-add (KOSPI200/KOSDAQ150) + name/code search; portfolio is **long-only** + concurrent-holding cap (top by liquidity). Index constituents are **disk-cached** (`etl.universe.index_members_cached` → `data/krx/index_members.json`, 7-day TTL) so the dashboard bulk-add button doesn't re-hit KRX (login + portal scrape) on every click — membership only changes ~quarterly
- **Index SSOT** — 인덱스 정의는 `etl.universe.INDEXES`(`[{key,code,label}]`) **단일 출처**. 새 내장 인덱스 = 거기 **한 줄** 추가하면 세 화면(분봉적재 버튼 / **적재현황 인덱스 필터** / 백테스트 종목선택 버튼)에 자동 반영. 라우트가 `config.all_indices()`(내장+커스텀 통합)를 컨텍스트로 넘겨 Jinja `{% for %}`로 동적 렌더(인덱스명 하드코딩 제거). `INDEX_CODES`/`KOSPI200_INDEX`는 파생값으로 하위호환. 적재현황(`/data`)은 인덱스 필터 드롭다운으로 목록을 구성종목(적재된 것만, `/universe/index/{key}`)으로 거른다(검색과 AND)
- **커스텀 인덱스(사용자 정의 종목 묶음)** — `config.local.yaml` `custom_indices:{이름:{label,tickers}}`에 저장(config CRUD: `get_custom_indices`/`save_custom_index`/`delete_custom_index`). `all_indices()`가 내장+커스텀을 합쳐 노출(커스텀은 `★` 라벨, 내장명과 충돌 거부). `/universe/index/{name}`이 커스텀이면 저장 종목(적재분 교집합)을 반환 → fetch 기반인 세 화면에서 **내장 인덱스와 동일하게** 사용(데이터/백테스트는 '사용'만). 관리(생성/**수정**/삭제)는 **전용 '그룹' 탭**(`/groups`, `groups.html`)에서 종목 검색·칩으로. 수정은 '수정' 버튼이 이름·종목을 폼에 로드 → 저장 시 덮어쓰기, 이름을 바꾸면 rename(`original_key`로 옛 키 삭제). (`/universe/custom`, `/universe/custom/delete`)
- **Risk management** (global per-security stop-loss/take-profit/trailing); config & secrets (env → config.local.yaml → dashboard)
- **Broker selection** (`config.get_broker`, dashboard) — **kis(실전)/kis_demo(모의)/toss**; KIS 실전·모의는 키·계좌 시크릿이 완전 분리(`BROKER_SECRET_SPECS["kis"|"kis_demo"]`, `get_kis_credentials(broker)`). env는 증권사가 결정(`broker_env`) — 라이브 매매 env·자동매매 무장 가드에 사용. per-broker secrets separate from always-needed pykrx login
- **KIS data layer** — `brokers/kis.py` `KisClient` (OAuth + disk-cached token), daily (수정주가) + `fetch_today` + minute (`fetch_minute`); selectable ETL source (`etl.sources.KisSource`); minute ETL → LEAN minute format (`etl/kis_minute.py`, `lean_format.write_equity_minute`)
- **Minute ingestion (data tab)** — `/data/minute` job ingests minute bars for selected tickers + index bulk-select (KOSPI200/KOSDAQ150), period 1m–1y; **incremental skip** of days already on disk + clamp to KIS's ~1y window (`ingest_minute(skip_existing, today)`). **Parallel by trading day** (`ThreadPoolExecutor`, `max_workers`) gated by a **shared thread-safe token bucket** in `KisClient` (`_TokenBucket`, `rate_per_sec`) — concurrency hides network RTT while aggregate call rate stays under KIS's limit (real ~20/s; minute job uses 12/s × 8 workers). The old per-call `min_interval` throttle (not thread-safe) is replaced; `min_interval` kept for back-compat (→ derived rate)
- **Minute seed data (fast start)** — bulk minute history is distributed via a **GitHub Release asset** (tag `minute-seed`, not committed — keeps clones light; `data/` stays gitignored). `scripts/fetch_minute_seed.sh` (user, curl, no auth) downloads+extracts into `data/`; `scripts/make_minute_seed.sh` (maintainer, `gh`) tars `data/equity/krx/minute` and `--clobber`s the release. Minute zips are write-once so re-packaging is safe. Users fill gaps via the 분봉 최신화 button (incremental)
- **Resolution-driven config (전략 설정 UI)** — 리스크 관리 + 체결을 하나의 카드로 합치고 **해상도(일봉/분봉)가 나머지를 결정**한다(사용자가 일일이 안 고르게; `execution_from_form` derives select_eval/risk_eval from resolution):
  - **일봉**: 선별=전날 종가 1회(`select_eval=close`), 리스크 평가=종가 1회(`risk_eval=daily`), 체결=**다음 거래일 시가/종가** (`daily_fill`: `open`=프레임워크 기본 시장가 | `close`=`MarketOnClose`, `strategies/daily_execution.py` `DailyExecutionModel`; 신호는 당일 종가 계산 → 룩어헤드 방지로 체결은 다음 거래일). 분봉 유무 무관.
  - **분봉**: 선별=장중 매분(`select_eval=intraday`), 리스크 평가=매분(`risk_eval=bar`), 체결=**TWAP 고정**(`style=twap`; 사용자는 **분할 수**만 지정). 눌림목은 장중 매분 선별과 가격 반응 역할이 겹쳐(같은 축) 제외 — TWAP는 "수량을 시간에 분산"이라 선별과 직교. immediate는 폴백 전용. 분봉 있으면 TWAP, **없는 종목/일은 일봉(시가)로 자동 폴백**(`TimingConfig.for_availability`, `lean_format.list_minute_days`)
- **Intraday timing layer (②)** — `Resolution.MINUTE` runs daily selection + intraday execution; pure logic unit-tested (`orchestrator/execution.py`). 장중 매분 선별: price signals re-evaluated each minute with the forming day's bar (prior daily closes + current price, pure `orchestrator/indicators.py`; 수급·가치 stay prior-close). Whipsaw guard: same-day re-entry blocked (cooldown). No look-ahead (data ≤ now)

**Live (KIS) — built, gated behind arming:**
- **KIS live adapter** (`adapter/MyTrading.Kis`, `MyTrading.Kis.dll`) — C# `KisBrokerage` (+`IDataQueueHandler`): `KisRestClient` (token cache, order-cash `TTTC0012U`/`0011U`, order-rvsecncl, inquire-balance, inquire-psbl-order, chk-holiday; real/demo TR 분기), `KisWebSocketClient` (실시간 체결가 `H0STCNT0` → feed, 체결통보 `H0STCNI0`/`9` AES-CBC → OrderEvent), `KisSymbolMapper`, `KisBrokerageModel` (`Market.Add("krx",50)`, `KoreanFeeModel` matching `market/krx.py`), `KisBrokerageFactory`. Builds to net10 against LEAN NuGet `2.5.17757`; `scripts/build-adapter.sh` copies the DLL next to the launcher so Composer loads `KisBrokerage` by name.
- **Live wiring** — `runner.build_live_config` emits the `live-kis` LEAN environment (live handlers + `BrokerageSetupHandler` + brokerage data); `runner.run_live` spawns it. `config.get_live_config`/`live_arming_ok` + the brokerage's own `PlaceOrder` gate enforce **arming**: real orders never transmit unless armed, with a per-order 원 cap; defaults disabled/unarmed/demo. Tests: `tests/test_live.py` (pure config/builder), `adapter/MyTrading.Kis.Tests` (xUnit frame parsing/constants). See **[docs/LIVE_KIS.md](./docs/LIVE_KIS.md)**.

**매매(라이브) 대시보드 탭 — built (control + monitoring surface):**
- **매매 탭** (`/trade`, `trade.html`; nav '● 매매' 강조 버튼) — 레이아웃은 전략설정처럼 `wide` 2-col:
  최상단 **A** 증권사/계좌(마스킹·실전/모의 배지), 그 아래 **D** 자동매매 on/off 큰 토글 + 무장/환경/한도 +
  **E** 장상태 배지(장중/장시작전/장마감/휴장, KST), 1열 **B** 예수금·매수가능·보유종목(매수가/현재가/평가/손익),
  2열 **C** 매매내역(날짜 picker + ◀▶ 인접 거래일 + 일별 실현손익).
- **브로커 무관 읽기 계층** — `brokers/base.py` `TradingBroker`(KIS∩Toss 교집합: account_info/balance/market_status)
  + `brokers/kis_broker.py` `KisBroker`(KisClient 래핑, Asia/Seoul 시각으로 장중 판정). KIS 읽기 메서드
  `KisClient.fetch_balance`(보유+예수금)·`check_market_open`(chk_holiday)·`fetch_executions`(체결내역,
  inquire-daily-ccld) 추가(real/demo TR 분기). `KisBroker.trades(date)`가 체결조회를 매매내역 dict로.
- **C 매매내역 = 브로커 체결조회 우선(KIS 실거래)** — `KisBroker.trades(date)`가 KIS `inquire-daily-ccld`로
  계좌의 실제 체결(앱·HTS·자동매매 무관)을 보여준다. 체결조회 미지원 브로커(Toss 등)는 buylow 자체
  거래로그(`TradeStore`, SQLite)로 폴백. 화살표는 달력 ±1일(체결조회는 임의 날짜 조회 가능). 체결조회엔
  실현손익이 없어 손익 합은 자체 로그가 있을 때만 표시(`has_pnl`). **잔고/보유종목은 `inquire-balance`라
  KIS 앱 매수가 자동 반영**.
- **B/C 백그라운드 캐시 + 비동기 로드** — KIS 잔고·체결조회가 느려 화면이 직접 기다리지 않는다.
  **`orchestrator/broker_cache.py` `BrokerCache`**: 서버 가동 동안(FastAPI `lifespan`) 백그라운드 스레드가
  10초마다 **활성 증권사의 잔고 + 당일 체결을 메모리 캐시**. 라우트(`/trade/balance`·`/trade/trades`)는 캐시를
  즉시 반환(KIS 왕복 없음). 캐시 미스(첫 진입·과거 날짜)는 동기 1회로 메우고, 증권사/키 변경 시 `invalidate()`.
  화면: 진입 시 A/E만 서버 렌더 + B/C는 `loading` 자리표시 → HTMX `hx-trigger="load, every 10s"`로 캐시 표시.
  날짜 **화살표/선택은 `#trade-trades`만 부분 교체**(전체 리로드/브라우저 탭 로딩 없음). 헤더에 캐시 기준시각 표시.
- **D 자동매매 가동(LEAN 라이브)** — 매매 탭에서 **대상종목(라이브 유니버스)**을 인덱스·그룹·검색 칩으로
  골라 `POST /trade/universe`(`config.save_live_universe`)로 저장. 토글 ON(`/trade/toggle`)이 가드
  (무장·전략저장·유니버스·어댑터 DLL) 통과 시 저장 전략+유니버스로 live-kis spec을 만들어
  `LiveProcessManager.start`(`orchestrator/live_runner.py`)로 **LEAN 라이브 프로세스 spawn**, OFF면
  `stop()`으로 종료(킬 스위치). `run_live(proc_sink=...)`가 Popen 핸들을 매니저에 넘겨 terminate/kill.
  해상도는 저장 전략의 resolution(분봉=1분봉마다, 일봉=다음 거래일). `RuleStrategy`는 라이브 시 start/end/cash
  미설정(현재시각·계좌잔액). v1 계좌당 1전략. ⚠️ 실주문 e2e는 모의(demo)+어댑터 빌드 수동 검증(LIVE_KIS.md).

**Not done / gated:**
- ⛔ **Real-order e2e** — needs a KIS account; verify on **모의(demo)** first (어댑터 빌드 + 절차 LIVE_KIS.md). Real(real)은 검증 전까지 무장 금지. **토글→LEAN 라이브 spawn/kill·라이브 유니버스는 구현됨**(위 D). 남은 것: 라이브 프로세스 헬스/재시작 감독, open-order resync on restart, precise fill-fee reporting, 체결통보 HTS-ID 의존, 실전 무장 UI.
- ⛔ **Toss live** — same `IBrokerage` shape; gated on Toss API (not open).
- ⚠️ **KIS minute history is bounded** (~1y kept, 120 bars/call) → minute backtest is universe-scoped + recent, not whole-market 5y.
- Volatility-breakout (intraday signals), parameter optimization (sweep), OpenDART deep financials, news/sentiment, universe criteria pre-filter, custom risk (ATR/vol), PCM selection, equity charts, alerts, named strategies, cross-platform packaging, LICENSE.
- (No AI/NL strategy generation — intentionally out of scope.)

> See `README.md` 로드맵 for the up-to-date checklist.

> A local clone of [QuantConnect/Lean](https://github.com/QuantConnect/Lean) is a useful
> read-only reference (interfaces, sample data). Its path is machine-specific — never commit it.
