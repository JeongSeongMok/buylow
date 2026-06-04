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
| Brokerage model | User picks broker in dashboard (KIS=한국투자증권 now; Toss when API opens). Broker drives "today/live" data + (live) orders; historical daily stays keyless pykrx |
| Two-layer strategy | ① daily selection (alpha, once/day) + ② intraday timing (LEAN `ExecutionModel` on minute bars). Same code in backtest & live. Timing logic is pure (`orchestrator/execution.py`) + thin adapter (`strategies/intraday_execution.py`) — mirrors `rules.py`/`RuleAlpha` |
| C# artifacts | **Two**: a thin net10 launcher (vendored LEAN `Program.cs`) + a broker adapter DLL (`MyTrading.Toss.dll` / `MyTrading.Kis.dll`) — **live only, not built yet** |
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
- Universe — scan all loaded + index bulk-add (KOSPI200/KOSDAQ150) + name/code search; portfolio is **long-only** + concurrent-holding cap (top by liquidity)
- **Risk management** (global per-security stop-loss/take-profit/trailing); config & secrets (env → config.local.yaml → dashboard)
- **Broker selection** (`config.get_broker`, dashboard) — KIS now; per-broker secrets separate from always-needed pykrx login
- **KIS data layer** — `brokers/kis.py` `KisClient` (OAuth + disk-cached token), daily (수정주가) + `fetch_today` + minute (`fetch_minute`); selectable ETL source (`etl.sources.KisSource`); minute ETL → LEAN minute format (`etl/kis_minute.py`, `lean_format.write_equity_minute`)
- **Minute ingestion (data tab)** — `/data/minute` job ingests minute bars for selected tickers + index bulk-select (KOSPI200/KOSDAQ150), period 1m–1y; **incremental skip** of days already on disk + clamp to KIS's ~1y window (`ingest_minute(skip_existing, today)`). **Parallel by trading day** (`ThreadPoolExecutor`, `max_workers`) gated by a **shared thread-safe token bucket** in `KisClient` (`_TokenBucket`, `rate_per_sec`) — concurrency hides network RTT while aggregate call rate stays under KIS's limit (real ~20/s; minute job uses 12/s × 8 workers). The old per-call `min_interval` throttle (not thread-safe) is replaced; `min_interval` kept for back-compat (→ derived rate)
- **Minute seed data (fast start)** — bulk minute history is distributed via a **GitHub Release asset** (tag `minute-seed`, not committed — keeps clones light; `data/` stays gitignored). `scripts/fetch_minute_seed.sh` (user, curl, no auth) downloads+extracts into `data/`; `scripts/make_minute_seed.sh` (maintainer, `gh`) tars `data/equity/krx/minute` and `--clobber`s the release. Minute zips are write-once so re-packaging is safe. Users fill gaps via the 분봉 최신화 button (incremental)
- **Intraday timing layer (②) for backtest** — `Resolution.MINUTE` runs daily selection + intraday execution; styles: pullback (default), TWAP slicing, immediate; pure logic unit-tested. **Per-(symbol,day) fallback**: days with minute data on disk use the timing style; days without fall back to open fill (`TimingConfig.for_availability`, `lean_format.list_minute_days`)
- **Selection cadence (①) option** — `select_eval`: `close`(전날 종가 1회, default, uses LEAN daily indicators) | `intraday`(장중 매분: price signals re-evaluated each minute with the forming day's bar = prior daily closes + current price, via pure `orchestrator/indicators.py`; 수급·가치 stay prior-close). Whipsaw guard: same-day re-entry blocked (cooldown). No look-ahead (uses data ≤ now). Pure indicators unit-tested; end-to-end needs minute data + LEAN run
- **Risk eval cadence option** — `risk_eval`: `bar`(매분) | `daily`(종가 1회, `DailyGatedRiskModel`). Execution-style note: under intraday selection, pullback overlaps selection (immediate/TWAP recommended)

**Not done / gated:**
- ⛔ **Live trading (real orders)** — KIS/Toss `IBrokerage`+`IDataQueueHandler` C# adapter DLL. **Design only** (per decision): backtest path complete; live execution intentionally not built — no real-order code ships without explicit user arming + a real account. Toss also gated on its API (not open).
- ⚠️ **KIS minute history is bounded** (~1y kept, 120 bars/call) → minute backtest is universe-scoped + recent, not whole-market 5y.
- Volatility-breakout (intraday signals), parameter optimization (sweep), OpenDART deep financials, news/sentiment, universe criteria pre-filter, custom risk (ATR/vol), PCM selection, equity charts, alerts, named strategies, cross-platform packaging, LICENSE.
- (No AI/NL strategy generation — intentionally out of scope.)

> See `README.md` 로드맵 for the up-to-date checklist.

> A local clone of [QuantConnect/Lean](https://github.com/QuantConnect/Lean) is a useful
> read-only reference (interfaces, sample data). Its path is machine-specific — never commit it.
