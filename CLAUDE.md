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
   Update it whenever a feature lands or the architecture changes. **Translations now exist:
   `README.en.md` (English) + `README.ja.md` (Japanese)** — `README.md` is the source of truth;
   when you change it, mirror the change into both translations (all three carry the same language
   switcher line under the title). (CLAUDE.md = working agreement + decisions; README = user overview.)
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
| C# artifacts | **Two**: a thin net10 launcher (vendored LEAN `Program.cs`) + a broker adapter DLL. `MyTrading.Kis` is **built** (`adapter/MyTrading.Kis`; no arming gate — only an optional per-order amount cap); `MyTrading.Toss` follows when Toss API opens |
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
- Data — one '데이터 최신화' path (button + **auto scheduler**): whole-market incremental per data type (price by-date, fundamentals by-date cross-section, flow per-ticker), 5y backfill when empty; code→name map (`etl.names`); dashboard shows latest loaded date, search, per-ticker detail with date filter
- **Auto scheduler** (`orchestrator/scheduler.py`, APScheduler) — **기본 켜짐**, 서버 가동 중 `scheduler.interval_minutes`(기본 30분) **간격 반복**(과거 평일 18:00 cron→IntervalTrigger; 증분이라 채워졌으면 금방 끝남). `max_instances=1`+`coalesce`로 겹침/밀림 방지. 한 틱(`run_scheduled`)에서 일봉(pykrx, `run_data_update`) + **분봉(KIS, `run_minute_update`)을 `scheduler.minute_universe`가 있을 때만** 순차 적재(매 틱 config 재독 → 대상 변경이 재시작 없이 반영). 일봉은 키리스라 무조건, 분봉은 증권사 API라 대상종목 지정 필요. 데이터 탭(`/data`)에 자동 스케줄 상태 카드 + 분봉 카드의 '자동적재 대상으로 저장'(`POST /data/schedule/minute`, `config.save_scheduler_minute_universe`; 분봉 선택 위젯이 현재 대상으로 초기화)
- Orchestrator — LEAN Runner, SQLite persistence, FastAPI Control API; dashboard tabs (전략 설정 / 백테스트 / 데이터 / 설정 / 작업중), landing = 전략 설정; 2-column layouts, no-wrap tables, scroll
- Background-job backtests with live log + progress %; run history; Korean result summary (억/만원) + **trade history** (date/name/side/qty/amount/reason; risk tag > signal reason)
- **백테스트 이력 삭제** — 실행 이력 행별 '삭제' + '전체 삭제'(`POST /ui/runs/{id}/delete`, `/ui/runs/clear`; `RunStore.delete_run`/`clear_runs` = DB 행, 라우트가 `_delete_run_dir`로 `runs/<id>/` blob까지 — GB 단위라 디스크 정리. repo `runs/` 하위만 rmtree 가드)
- **대량 거래내역(6만+ 건) 페이지네이션** — 분봉 백테스트는 거래가 수만 건이라 결과 JSON(수십~수백 MB)을 상세 진입마다 통째 파싱하면 매우 느림. 해법: 결과 JSON을 **1회만** 파싱해 슬림한 `trades.jsonl`(한 줄=한 거래, 최신순; 완료 run은 불변이라 1회 빌드 안전)로 캐시(`_ensure_trades_cache`)하고, 화면은 거기서 페이지 슬라이스만 읽음(`load_trades_page`, offset/limit≤500). 상세 페이지는 거래를 인라인 X — HTMX(`hx-trigger="load"`)로 `/ui/runs/{id}/trades?offset=&limit=`를 가져와 이전/다음 페이지네이션(`partials/trades_table.html`). 첫 진입 캐시 빌드도 비동기 로드 뒤라 화면 안 멈춤. `parse_orders`=`_rows_from_orders(_load_result_orders(...))`로 분해(캐시 빌드 재료). ⚠️ **LEAN 주문 truncation + 자체 체결로그**: LEAN은 결과 파일(`.json`·`-order-events.json` 둘 다)에 주문을 **0~100건만** 직렬화한다(대량 분봉 백테스트는 로그 폭주로 0건이 되기도) — 전체 주문은 LEAN 출력 어디에도 없다(통계 `Total Orders`는 전체라 정확). **해결**: 전략이 `on_order_event`에서 모든 체결을 직접 `run_dir/fills.jsonl`(LEAN order dict 형태)로 남긴다(`KrxFrameworkAlgorithm._setup_fill_log`/`on_order_event`, 경로는 Runner가 `trade_log` 파라미터로 주입). 대시보드는 `fills.jsonl`(완전)을 1순위로, 없으면 결과 JSON으로 폴백(`_load_fills`→`_rows_from_orders`). 캐시 사이드카(`trades.meta.json`)에 `orders_in_result`+`complete` 기록 — `complete`(체결로그 기반)면 truncation 고지 안 함, 폴백이고 `Total Orders` > `orders_in_result`이면 거래내역 상단에 고지(silent cap 금지). **체결로그는 이 변경 이후 새로 돌린 백테스트부터 생김**(기존 런은 폴백·고지)
- **Rule engine** — condition-group builder (AND within group, OR across groups); single persisted strategy; signal-hold period
- **Signals (7)** — EMA, MACD, RSI, momentum, Bollinger (mean-reversion + breakout switch), value (저PER/저PBR + derived ROE), flow (수급: foreign/inst/individual selectable, N-day cumulative)
- Universe — scan all loaded + index bulk-add (KOSPI200/KOSDAQ150) + name/code search; portfolio is **long-only** + concurrent-holding cap (top by liquidity). Index constituents are **disk-cached** (`etl.universe.index_members_cached` → `data/krx/index_members.json`, 7-day TTL) so the dashboard bulk-add button doesn't re-hit KRX (login + portal scrape) on every click — membership only changes ~quarterly
- **Index SSOT** — 인덱스 정의는 `etl.universe.INDEXES`(`[{key,code,label}]`) **단일 출처**. 새 내장 인덱스 = 거기 **한 줄** 추가하면 세 화면(분봉적재 버튼 / **적재현황 인덱스 필터** / 백테스트 종목선택 버튼)에 자동 반영. 라우트가 `config.all_indices()`(내장+커스텀 통합)를 컨텍스트로 넘겨 Jinja `{% for %}`로 동적 렌더(인덱스명 하드코딩 제거). `INDEX_CODES`/`KOSPI200_INDEX`는 파생값으로 하위호환. 적재현황(`/data`)은 인덱스 필터 드롭다운으로 목록을 구성종목(적재된 것만, `/universe/index/{key}`)으로 거른다(검색과 AND)
- **커스텀 인덱스(사용자 정의 종목 묶음)** — `config.local.yaml` `custom_indices:{이름:{label,tickers}}`에 저장(config CRUD: `get_custom_indices`/`save_custom_index`/`delete_custom_index`). `all_indices()`가 내장+커스텀을 합쳐 노출(커스텀은 `★` 라벨, 내장명과 충돌 거부). `/universe/index/{name}`이 커스텀이면 저장 종목(적재분 교집합)을 반환 → fetch 기반인 세 화면에서 **내장 인덱스와 동일하게** 사용(데이터/백테스트는 '사용'만). 관리(생성/**수정**/삭제)는 **전용 '그룹' 탭**(`/groups`, `groups.html`)에서 종목 검색·칩으로. 수정은 '수정' 버튼이 이름·종목을 폼에 로드 → 저장 시 덮어쓰기, 이름을 바꾸면 rename(`original_key`로 옛 키 삭제). (`/universe/custom`, `/universe/custom/delete`)
- **Risk management** (global per-security stop-loss/take-profit/trailing); config & secrets (env → config.local.yaml → dashboard)
- **Broker selection** (`config.get_broker`, dashboard) — **kis(실전)/kis_demo(모의)/toss**; KIS 실전·모의는 키·계좌 시크릿이 완전 분리(`BROKER_SECRET_SPECS["kis"|"kis_demo"]`, `get_kis_credentials(broker)`). env는 증권사가 결정(`broker_env`) — 라이브 매매 env(real/demo 도메인)에 사용. per-broker secrets separate from always-needed pykrx login
- **KIS data layer** — `brokers/kis.py` `KisClient` (OAuth + disk-cached token), daily (수정주가) + `fetch_today` + minute (`fetch_minute`); selectable ETL source (`etl.sources.KisSource`); minute ETL → LEAN minute format (`etl/kis_minute.py`, `lean_format.write_equity_minute`)
- **Minute ingestion (data tab)** — `/data/minute` job ingests minute bars for selected tickers + index bulk-select (KOSPI200/KOSDAQ150), period 1m–1y; **incremental skip** of days already on disk + clamp to KIS's ~1y window (`ingest_minute(skip_existing, today)`). **Parallel by trading day** (`ThreadPoolExecutor`, `max_workers`) gated by a **shared thread-safe token bucket** in `KisClient` (`_TokenBucket`, `rate_per_sec`) — concurrency hides network RTT while aggregate call rate stays under KIS's limit (real ~20/s; minute job uses 12/s × 8 workers). The old per-call `min_interval` throttle (not thread-safe) is replaced; `min_interval` kept for back-compat (→ derived rate)
- **Minute seed data (fast start)** — bulk minute history is distributed via a **GitHub Release asset** (tag `minute-seed`, not committed — keeps clones light; `data/` stays gitignored). `scripts/fetch_minute_seed.sh` (user, curl, no auth) downloads+extracts into `data/`; `scripts/make_minute_seed.sh` (maintainer, `gh`) tars `data/equity/krx/minute` and `--clobber`s the release. Minute zips are write-once so re-packaging is safe. Users fill gaps via the 분봉 최신화 button (incremental)
- **Timing-driven config (전략 설정 UI)** — **종목 선별은 항상 전날 데이터 1회(`select_eval=close`, 장중 재선별 없음)**. 사용자는 '체결 타이밍'만 고르고, 타이밍이 해상도·리스크주기를 자동 도출(`execution_from_form` → `EXECUTION_TIMINGS`). `timing` ∈ {open, close, time, twap, pullback}:
  - **open/close** → resolution=daily, risk=daily, 분봉 데이터 불필요. `daily_fill`=open(프레임워크 기본 시장가, 다음 거래일 시가) | close(`MarketOnClose`, `strategies/daily_execution.py`). 신호는 전날 종가 → 룩어헤드 방지로 체결 다음 거래일. ⚠️ **종가 라이브 괴리**: 백테스트는 다음날 정확한 종가(15:30)지만 KIS 어댑터에 종가 단일가 주문이 없어 라이브는 마감 ~15.5분 전(15:14쯤) 일반 시장가(룩어헤드는 없음). UI가 close 선택 시 주의문 노출. 일치는 open 권장
  - **리스크 평가는 항상 종가 1회**(`risk_eval=daily`, 분봉이면 `DailyGatedRiskModel`이 마감 1회) — 선별과 같은 철학. ⚠️ 분봉 매분 손절/트레일링은 장중 노이즈(±8%)에 계속 발동해 과매매(회전율 78%↑, 수수료 3배)를 일으키므로 폐기(통제실험: 매분→일별로 회전율 78%→38%·수수료 절반). 청산 '판단'은 종가, '체결'은 아래 타이밍이 처리
  - **time/twap/pullback** → resolution=minute, `IntradayExecutionModel`(`strategies/intraday_execution.py`) 스타일=`time`(at_min 시각 전량)|`twap`(slices 분할)|`pullback`(entry_drop/exit_rebound %). 분봉 있으면 그대로, 없는 (종목,일)은 시가 즉시 폴백(`TimingConfig.for_availability`). 순수 로직 단위테스트(`orchestrator/execution.py` `decide_submit`/`TIME`/`TWAP`/`PULLBACK`)
  - ⚠️ **분봉 가용성 경로**: RuleStrategy가 `list_minute_days(spec.data_folder,...)`로 폴백 판정 — `data_folder`는 반드시 **절대경로**(LEAN cwd=런처폴더라 상대경로면 빈 결과→잘못된 시가 폴백). spec 빌드 시 `Path(...).resolve()`
  - **장중 재선별 제거됨**(과거 `select_eval=intraday`+`eval_cadence`): 매분 재선별이 과매매·수수료의 원인이라 폐기. 선별은 전날 1회로 고정, 분봉은 체결 타이밍에만 사용. ⚠️ **분봉 타이밍은 아직 유니버스 전체 분봉 구독** → `종목수×거래일 ≲ 10,000` 한도 가드 유지. "보유종목만 분봉 동적구독"(한도 완화)은 LEAN 프레임워크 동적구독 제약으로 후속(혼합해상도는 가능 확인 `MixedResProbe`)
- **Two-layer**: ① 선별(`RuleAlpha`, 전날 일봉 신호 1회) + ② 체결 타이밍(daily/minute 실행모델). 같은 코드 백테스트·라이브. No look-ahead

**Live (KIS) — built (자동매매 토글 ON이면 실전·모의 모두 바로 매매; 무장 개념 제거):**
- **KIS live adapter** (`adapter/MyTrading.Kis`, `MyTrading.Kis.dll`) — C# `KisBrokerage` (+`IDataQueueHandler`): `KisRestClient` (token cache, order-cash `TTTC0012U`/`0011U`, order-rvsecncl, inquire-balance, inquire-psbl-order, chk-holiday; real/demo TR 분기), `KisWebSocketClient` (실시간 체결가 `H0STCNT0` → feed, 체결통보 `H0STCNI0`/`9` AES-CBC → OrderEvent), `KisSymbolMapper`, `KisBrokerageModel` (`Market.Add("krx",50)`, `KoreanFeeModel` matching `market/krx.py`), `KisBrokerageFactory`. Builds to net10 against LEAN NuGet `2.5.17757`; `scripts/build-adapter.sh` copies the DLL next to the launcher so Composer loads `KisBrokerage` by name.
- **Live wiring** — `runner.build_live_config` emits the `live-kis` LEAN environment (live handlers + `BrokerageSetupHandler` + brokerage data); `runner.run_live` spawns it. **무장(arming) 게이트는 제거됨** — 시작 가드는 `config.live_start_ok`(=`enabled` + **HTS ID 필수**)다. HTS ID는 체결통보 구독에 필요(없으면 주문이 LEAN에 체결로 반영되지 않아 포지션/리스크 추적이 어긋남)해 라이브 시작을 막는다. **HTS ID는 설정 탭의 증권사별 시크릿**(`kis_hts_id`/`kis_demo_hts_id`, app_key와 동일 관리·실전/모의 분리; `config.get_kis_hts_id`)으로 등록 — `get_live_config.hts_id`가 활성 증권사 시크릿에서 도출돼 `kis-hts-id`로 주입된다. 유일한 선택적 방벽은 `max_order_amount`(원, 0=비활성, 매매 탭 `/trade/arm`)로 `KisBrokerage.PlaceOrder`가 1건 금액 초과 시 거부. defaults disabled/demo. Tests: `tests/test_live.py` (pure config/builder), `adapter/MyTrading.Kis.Tests` (xUnit frame parsing/constants). See **[docs/LIVE_KIS.md](./docs/LIVE_KIS.md)**.

**매매(라이브) 대시보드 탭 — built (control + monitoring surface):**
- **매매 탭** (`/trade`, `trade.html`; nav '● 매매' 강조 버튼) — 레이아웃은 전략설정처럼 `wide` 2-col:
  최상단 **A** 증권사/계좌(마스킹·실전/모의 배지), 그 아래 **D** 자동매매 on/off 큰 토글(무장 없음, 켜면 바로 매매) +
  **E** 장상태 배지(장중/장시작전/장마감/휴장, KST), 1열 **B** 예수금·매수가능·보유종목(매수가/현재가/평가/손익),
  2열 **C** 매매내역(날짜 picker + ◀▶ 인접 거래일 + 일별 실현손익).
- **오늘의 선정(담을/뺄 종목 미리보기)** — `/trade/selection` 부분(`partials/trade_selection.html`, B/C 위, 30초 HTMX 갱신). 선별은 '전날 종가 1회'(`RuleAlpha`)라 LEAN 없이 재현 가능 → `signal_diag.select_today(spec, data_dir, universe, held)`가 저장 전략 + 라이브 유니버스 + 캐시 잔고 보유종목으로 각 종목을 **자기 최신 일봉 날짜** 기준 1회 평가(`_direction`/`eval_rule` 재사용, analyze_run과 동일한 순수 재현). 반환: buys(rule UP; `held`로 신규/유지 구분) · sells(rule DOWN **이면서 보유** — RuleAlpha '보유 중일 때만 청산'과 동일) · cut(`max_positions` 초과로 유동성 하위 제외) · missing/stale/unmanaged 고지. 라우트는 `broker_cache.get_balance()`의 `items`에서 보유티커를 뽑아 넘김. 초기 렌더는 `loading` placeholder(trade_balance 패턴). 종목명은 라우트가 `names`로 매핑. **데이터 미적재 주의문**: 매매 선별도 적재 일봉을 읽으므로 `trade_page`가 `data_loaded=_loaded_count()`를 넘겨, 0이면 `trade.html` 상단에 백테스트 탭과 같은 '데이터 최신화 먼저' 주의문을 띄움(최초 사용자 안내)
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
  (enabled·**HTS ID**·전략저장·유니버스·어댑터 DLL; 무장 없음) 통과 시 저장 전략+유니버스로 live-kis spec을 만들어
  `LiveProcessManager.start`(`orchestrator/live_runner.py`)로 **LEAN 라이브 프로세스 spawn**, OFF면
  `stop()`으로 종료(킬 스위치). `run_live(proc_sink=...)`가 Popen 핸들을 매니저에 넘겨 terminate/kill.
  해상도는 저장 전략의 resolution(분봉=1분봉마다, 일봉=다음 거래일). `RuleStrategy`는 라이브 시 start/end/cash
  미설정(현재시각·계좌잔액). v1 계좌당 1전략. ⚠️ 실주문 e2e는 모의(demo)+어댑터 빌드 수동 검증(LIVE_KIS.md).

**Not done / gated:**
- ⛔ **Real-order e2e** — needs a KIS account; verify on **모의(demo)** first (어댑터 빌드 + 절차 LIVE_KIS.md). ⚠️ **무장 게이트를 제거**해 실전(real)도 토글 ON이면 바로 실주문이 나가므로, real은 실계좌 검증 전까지 켜지 말 것(또는 `max_order_amount`로 1건 금액 상한). **토글→LEAN 라이브 spawn/kill·라이브 유니버스는 구현됨**(위 D). 남은 것: 라이브 프로세스 헬스/재시작 감독, open-order resync on restart, precise fill-fee reporting(라이브 `OnFill`은 현재 수수료 0 보고). **체결통보는 HTS ID 필수**(설정 탭 시크릿)로 강제 — 없으면 `live_start_ok`가 라이브 시작을 막는다(REST 체결폴링 폴백은 미구현).
- ⛔ **Toss live** — same `IBrokerage` shape; gated on Toss API (not open).
- ⚠️ **KIS minute history is bounded** (~1y kept, 120 bars/call) → minute backtest is universe-scoped + recent, not whole-market 5y.
- ⚠️ **LEAN 분봉 백테스트 규모 한도** — 동시구독 **종목수 × 거래일 ≳ 10,000**을 넘으면 LEAN 데이터피드 read-ahead가 멈추고 **fill-forward(마지막 봉 복제)**로 가짜봉을 채워 결과를 조용히 오염시킨다(데이터/전략 문제 아님 — 맨 알고리즘 `strategies/MinuteFeedProbe.py`로 통제실험 확인: 40종목×1년≈9,800 깨끗, 200·348 동결. 단일 config 상수 아닌 read-ahead 버퍼/워크스케줄러/zip캐시의 창발 천장). **가드**: `POST /backtest`가 분봉+`len(universe)×_trading_days > MINUTE_FEED_MAX_SYMBOL_DAYS(10,000)`이면 차단(최대 종목수 안내). 곱 한도라 1년≈40종목·3개월≈150·1개월≈400. 일봉·라이브는 무관(라이브는 실시간 1봉씩, read-ahead 없음). 메모리 `lean-minute-feed-scale-limit`
- Volatility-breakout (intraday signals), parameter optimization (sweep), OpenDART deep financials, news/sentiment, universe criteria pre-filter, custom risk (ATR/vol), PCM selection, equity charts, alerts, named strategies, cross-platform packaging, LICENSE.
- (No AI/NL strategy generation — intentionally out of scope.)

> See `README.md` 로드맵 for the up-to-date checklist.

> A local clone of [QuantConnect/Lean](https://github.com/QuantConnect/Lean) is a useful
> read-only reference (interfaces, sample data). Its path is machine-specific — never commit it.
