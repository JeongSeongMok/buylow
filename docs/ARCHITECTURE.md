# Architecture

> The overall system design and the reasoning behind it. For setup/build instructions see
> [DEVELOPMENT.md](./DEVELOPMENT.md); for the user-facing overview see the [README](../README.md).

## In one line

A long-running **Python orchestrator** (which also serves a local browser dashboard) spawns
a **LEAN (.NET) process per task** to run a strategy in backtest or live mode. The *same
strategy code* runs in both — LEAN's backtest–live isomorphism.

## Design principles

These constraints drive every decision below:

1. **Clone-and-run, open source.** Users `git clone`, install prerequisites, and run. No
   separate servers to install (no MySQL/Redis/Elasticsearch). Source-only; no prebuilt installer.
2. **Bring your own keys (BYO).** Toss/AI secrets are supplied by each user and stored
   locally; **no secrets in the repo**.
3. **LEAN is never modified or forked.** It is referenced via NuGet and extended only
   through plugins (a DLL) and config.
4. **Backtest = live isomorphism.** One strategy `.py` runs in both modes; only the
   generated config differs.

## Component map

```
                          Browser (localhost:<port>, default 8420)
                                 │  HTTP + SSE
┌────────────────────────────────┼───────────────────────────────────────────┐
│ ORCHESTRATOR (Python, always-on)▼                                            │
│  ┌────────────── Control API + Dashboard (FastAPI, 127.0.0.1 only) ───────┐  │
│  │  strategy on/off · params · schedule · run backtests · view results    │  │
│  │  · live control + kill switch · key entry · AI strategy generation     │  │
│  │  (dashboard is a client of the API: HTMX + Jinja)                      │  │
│  └────────────────────────────────┬───────────────────────────────────────┘  │
│   ┌───────────────┬────────────────┼────────────────┬───────────────────────┐ │
│   ▼               ▼                ▼                ▼                       ▼ │
│ Scheduler    Strategy Registry  Config/Secrets   Persistence            AI Svc│
│ (APScheduler)(strategy catalog) (env→disk→UI)    (SQLite + files)       (NL→py)│
│   └───────────────┴────────────────┬────────────────┴───────────────────────┘ │
│                            ┌────────▼─────────┐                                │
│                            │   LEAN Runner    │ build config · set env · spawn │
│                            └────────┬─────────┘ · collect & parse results      │
└─────────────────────────────────────┼──────────────────────────────────────────┘
            inject config.json ▼        │ collect result files & logs ▲  monitor/kill
┌─────────────────────────────────────┼──────────────────────────────────────────┐
│ LEAN PROCESS (.NET 10, "one process = one job")                                 │
│   thin launcher → LEAN engine (unmodified NuGet) → strategy (.py)               │
│                                       + MyTrading.Toss.dll                       │
│                                         (TossBrokerage · DataQueue · KRX · Fees) │
└──────▲────────────────────────────────────────────────────▲────────────────────┘
       │ historical data (data/, LEAN format)                │ live quotes / orders
  ┌────┴─────────────┐                                  ┌────┴───────────────┐
  │ ETL (Python)      │  KRX/vendor → LEAN format         │ Toss API            │
  └───────────────────┘                                  └─────────────────────┘
```

## Two-process model

- **Orchestrator** — the only always-on process. Decides what/when/how to run, serves the
  dashboard, owns persistence. Does **not** use the Toss DLL.
- **LEAN process** — "one process = one job". Backtests are short-lived; live strategies are
  long-lived (one process per strategy). The orchestrator spawns/monitors/kills them.

## Orchestrator ↔ LEAN boundary

Because LEAN is a per-job executor, the two communicate via the **filesystem + process
control** — LEAN's native model, and the simplest robust option.

```
1. (in)  LEAN Runner writes a per-job config.json:
           environment: backtesting | live-toss
           algorithm-location / type: the strategy .py / class
           parameters: strategy parameters       (+ Toss keys injected only for live jobs)
         and sets env (PYTHONNET_PYDLL / PYTHONPATH), then spawns BuylowLauncher.dll.
2. (out) LEAN writes result files (statistics JSON, orders, equity curve) + stdout logs.
         The Runner parses them → stores summary/metadata in SQLite + raw blobs in runs/<id>/.
3. (ctl) The Runner monitors liveness / exit code and can kill (live kill switch).
```

`scripts/run-backtest.sh` is the manual version of step 1; the **LEAN Runner** absorbs it
into code.

## Strategy lifecycle (one `.py`, both modes)

```
strategy.py + parameters ─┬─ [backtest] env=backtesting, historical data in data/  → statistics
                          └─ [live]     env=live-toss,   Toss keys                  → real orders
```

- Same file and parameters; only the generated config's `environment` differs.
- Parameters are injected via LEAN's `parameters` and read with `get_parameter()` (this also
  enables optimization later).
- **AI-generated strategies**: natural language → a `QCAlgorithm` `.py` in
  `strategies/generated/` → **must pass a backtest before it may go live** (a safety gate).
  LEAN serves as the validator, which pairs well with AI generation.

## Strategies (Alpha framework)

Multiple strategies are composed via LEAN's **Alpha framework**, not run as independent bots on
one account (which would conflict — one strategy buying what another sells). Each strategy is an
**`AlphaModel`** that emits `Insight`s; the algorithm's **PortfolioConstruction** model nets them
into **one target weight per symbol**, so there is no cross-strategy conflict.

- `strategies/krx_framework.py` — `KrxFrameworkAlgorithm` base: registers `krx`, sets KRW, attaches
  the Korean fee model via a security initializer, and defaults to
  `EqualWeightingPortfolioConstructionModel` + `ImmediateExecutionModel`.
- `strategies/alphas.py` — the strategy catalog as alpha models (currently `EmaCrossAlpha` (trend),
  `BnfReversionAlpha` (mean reversion); all daily/price-based). Add more by type here.
- A concrete algorithm composes chosen alphas with `add_alpha(...)` over a universe
  (`strategies/KrxFrameworkExample.py` combines both on one symbol).
- Each strategy carries its own cadence (resolution + `Schedule.On`); rebalance frequency etc. are
  parameters. Minute/intraday strategies (e.g. volatility breakout) await a minute-bar ETL.

**Strategy registry (compose in the dashboard).** Available alphas are declared as pure specs in
`orchestrator/strategy_catalog.py` (name, params, defaults — no LEAN import, used for the UI). The
dashboard `/compose` lets the user pick alphas, set parameters, and choose a universe/dates; it
builds a composition spec (JSON) and runs the generic `strategies/Composed.py`, which reads the
spec via a `composition` parameter and instantiates the selected alphas through `alphas.build_alpha`.
The catalog uses **LEAN's built-in alpha models** (validated, market-agnostic) rather than custom
code: `ema_cross` (EmaCrossAlphaModel), `macd` (MacdAlphaModel), `rsi` (RsiAlphaModel), `momentum`
(HistoricalReturnsAlphaModel). `alphas.build_alpha` maps catalog names → LEAN classes. Adding a
built-in = one catalog entry + one factory line. **Korea-specific signals not in LEAN** are added as
custom alphas + custom data: `flow` (수급 추종, `KrxFlow` over `etl.flow`) goes long on accumulated
foreign net buying; `value` (저PBR, `KrxFundamental` over `etl.fundamental`) goes long when PBR is
below a threshold. BNF 이격도 등 추가 예정. Custom alphas live in `strategies/custom_alphas.py`;
custom data types in `strategies/krx_data.py`.

### Rule-based strategies (composable conditions)

Beyond OR-combining alpha models, the **rule engine** lets users compose conditions with full
boolean logic. Each **signal** returns `UP`/`DOWN`/`NONE` (not an insight), and a boolean
expression combines them, e.g. `(EMA AND MACD) OR (RSI AND MOM)`.

- `orchestrator/rules.py` — pure parser + 3-valued evaluator (AND = all same direction else NONE;
  OR = the unanimous non-conflicting direction else NONE). Shared by dashboard validation and LEAN.
- `orchestrator/signals_catalog.py` — signal specs (type, params) for the UI.
- `strategies/signals.py` — signal evaluators (thin wrappers over LEAN indicators: ema/macd/rsi/roc),
  with a `SIGNAL_TYPES` registry. Adding a condition = one class + one catalog entry.
- `strategies/RuleStrategy.py` — `RuleAlpha` evaluates each signal per symbol each step, evaluates the
  expression, and emits an UP/DOWN insight. The dashboard `/rules` builds the spec (signals + rule) and
  runs it. Same `RuleAlpha` will drive live (when Toss opens); intraday/30s eval awaits a live feed.
- Signals are evaluated as **states** (e.g. fast EMA > slow EMA) so AND/OR is meaningful each day.

### Risk management (global)

Entry is the strategy's job; **exit by P&L** (손절/익절/트레일링) is the **Risk stage** —
a different axis from rule-based selling. Risk is **global** (set in the 전략 설정 tab, stored in
`config.local.yaml` `risk:`), applied to **every** backtest and (later) live run: the `LeanRunner`
injects the configured percentages as `risk_*` parameters, and `KrxFrameworkAlgorithm` builds a
`CompositeRiskManagementModel` from LEAN's built-ins (`MaximumDrawdownPercentPerSecurity`,
`MaximumUnrealizedProfitPercentPerSecurity`, `TrailingStopRiskManagementModel`). These are
**per-security** stops; account-wide liquidation was intentionally dropped as too blunt for this
long-only style. Custom risk (ATR/volatility/time) later via the same hook.

## Dashboard

- A **browser UI** served by FastAPI on **`127.0.0.1:<port>`** (default `8420`, configurable
  in `config.local.yaml`). The user runs the app, then opens `localhost:<port>`.
- **HTMX + Jinja** (server-rendered, no Node/build step; charts via vendored JS). Real-time
  updates (run progress, live P&L, logs) via **Server-Sent Events (SSE)**. The frontend can
  later be swapped for an SPA without touching the API (the API is the contract).
- **Security:** binds to `127.0.0.1` only — it holds the user's Toss keys and trading control,
  so it must never be exposed to the network. A local token may be added if needed.

## Persistence

- **SQLite** — system of record for structured state: strategy config/on-off, run metadata &
  statistics, orders, positions, P&L history, schedules. Ships with Python (`sqlite3`), single
  file, **no server**. Run in WAL mode for concurrent reads.
- **Disk files** (`runs/<id>/`) — large blobs: result JSON, equity curves, logs (LEAN writes
  these directly). SQLite stores only the path + summary, not the blobs.
- **Ownership:** only the orchestrator (Python) writes SQLite; the LEAN process (C#) writes
  files. No cross-process DB contention.
- On startup the orchestrator reads SQLite to restore prior state for the dashboard, so
  closing and reopening the app shows previous content.
- Migration path: SQLite → Postgres only if multi-user/remote/scale is needed later.

## Configuration & secrets

```
config.local.yaml   (gitignored)   ← user's keys, enabled strategies, schedules, dashboard port
config.example.yaml (committed)    ← template with empty keys
```

Secrets (Toss app key/secret/account, AI provider/key) are **stored on disk** in
`config.local.yaml`. They are resolved in this order:

1. **Environment variables** (e.g. `BUYLOW_TOSS_APP_KEY`, `BUYLOW_TOSS_SECRET`,
   `BUYLOW_TOSS_ACCOUNT`, `BUYLOW_AI_API_KEY`) — highest priority; convenient for
   `export` before launch.
2. **On-disk config** (`config.local.yaml`).
3. **Dashboard prompt** — if neither is set, on first browser access the dashboard shows a
   setup screen to enter the keys, which are then written to `config.local.yaml`.

The orchestrator injects only what each job needs into its generated config (e.g. Toss keys
only for live jobs). Recommended: restrict the secrets file permissions (e.g. `chmod 600`).
(OS keychain storage is a possible future hardening.)

## Data / ETL

KRX/vendor data → **ETL (Python)** → LEAN format (zip+csv) + market-hours / symbol-properties
databases + universe data with **precomputed indicators** (e.g. 25-day MA / deviation) so that
whole-market universe scans are cheap. Provides historical data for backtests and for live
warm-up / indicators. The Toss API can also be a historical data source here.

## Live operations (planned)

- A live strategy is a long-lived LEAN process; the orchestrator supervises it (restart on
  crash, **kill switch**).
- On restart, position consistency is recovered via `BrokerageSetupHandler` calling
  `TossBrokerage.GetAccountHoldings` to sync from the real account.
- ⚠️ **v1: one live strategy per account.** Multiple live strategies on the same Toss account
  would conflict on positions; multi-strategy coordination is deferred.

## C# artifacts (two)

1. **Thin launcher** (`launcher/`) — a net10 console app that vendors LEAN's
   `Launcher/Program.cs` (Apache-2.0) verbatim and references the LEAN Engine NuGet. We build
   our own because the published launcher NuGet targets net462. Engine logic stays in the
   untouched NuGet; only the entry point is ours.
2. **`MyTrading.Toss.dll`** (`adapter/`) — the Korea/Toss adapter: `TossBrokerage`,
   `TossDataQueueHandler`, market definition (`Market.Add("krx")`, KRW), Korean fee/tax model.

## Directory structure

```
launcher/      C# thin launcher                                    (done)
adapter/       C# MyTrading.Toss (brokerage/dataqueue/KRX/fees)     (planned)
orchestrator/  Python: api/ · dashboard/ · core/ (registry,
               scheduler, jobmgr, config) · persistence/ (SQLite)
               · lean/ (runner) · ai/                              (planned)
strategies/    Python QCAlgorithm files (+ generated/)             (started)
etl/           Python: KRX → LEAN format                           (planned)
data/          LEAN-format market data (mostly gitignored)         (planned)
config/        config.example.yaml (config.local.yaml gitignored)  (planned)
runs/          per-run result blobs (gitignored)                   (planned)
scripts/  docs/  tests/
```

## Decisions

| Topic | Decision |
|---|---|
| LEAN usage | Reference via NuGet + extend with a plugin DLL (no fork) |
| C# artifacts | Two: thin net10 launcher + `MyTrading.Toss.dll` |
| LEAN NuGet version | `2.5.17757` (net10); never `10730.x` (net462) — see DEVELOPMENT.md |
| Process model | 2 processes; "one process = one job" |
| Orchestrator | Python (FastAPI + APScheduler) |
| Strategy language | Python (pythonnet) |
| Orchestrator ↔ LEAN | Filesystem (config in / results out) + process control |
| Persistence | SQLite (orchestrator-owned, WAL) for state + disk files (`runs/<id>/`) for blobs; no DB server |
| Control surface | Local browser dashboard via FastAPI on `127.0.0.1:<port>` (default 8420); HTMX+Jinja; SSE |
| Config & secrets | `config.local.yaml` (gitignored); secrets resolved env var → disk → dashboard prompt |
| Distribution | Open source, clone-and-run; BYO keys; no installer |
| AI strategies | NL → `.py`; mandatory backtest validation before live |
| Live multi-strategy | v1: one strategy per account |

## Build order

1. **LEAN Runner** — absorb `run-backtest.sh` into Python (build config, spawn, parse results)
2. **Persistence (SQLite)** + **Control API** skeleton
3. **Minimal dashboard** — run a backtest and view results
4. **KRX market definition + minimal ETL** — backtest a Korean symbol.
   *(4a done)* KRX market definition is implemented in **Python** (`market/krx.py`: KRW, Korean
   `korean_fee`, market-hours/symbol-properties injection; `strategies/krx.py`: `KrxAlgorithm`
   base + `KoreanFeeModel`). KRX backtests need only Python + config — the C# adapter is for live
   only. The runner puts the repo root on `PYTHONPATH` so strategies can import shared libs.
   *(4b next)* real Korean data ETL with a pluggable source (free provider now, Toss later).
5. Strategy registry & scheduling → **AI strategies** → richer dashboard
6. **Toss live adapter** — gated on Toss API availability (not open yet). It is the *only*
   piece that needs the broker API; everything above (backtest, KRX definition, ETL,
   dashboard, AI strategy generation + backtest validation) works without it.
