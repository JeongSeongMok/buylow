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
| C# artifacts | **Two**: a thin net10 launcher (vendored LEAN `Program.cs`) + `MyTrading.Toss.dll` adapter |
| LEAN NuGet version | `2.5.17757` lineage (net10); **never** `10730.x` (net462) — see DEVELOPMENT.md |
| Orchestrator ↔ LEAN | Filesystem (config in / results out) + process control |
| Persistence | SQLite (orchestrator-owned, WAL) for state + disk files (`runs/<id>/`) for blobs; no DB server |
| Control surface | Local browser dashboard via FastAPI on `127.0.0.1:<port>` (default 8420, configurable); HTMX+Jinja; SSE |
| Config & secrets | `config.local.yaml` (gitignored); secrets resolved env var → disk → dashboard prompt |
| AI strategies | NL → `.py`; mandatory backtest validation before live |
| Distribution | Open source, clone-and-run; BYO API keys; no installer |

## 2. Where to look

- **[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)** — system design, LEAN's role, the seams we build, distribution model, data flow, roadmap
- **[docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md)** — environment setup, build/run, the NuGet version trap, smoke test, validation log
- **[README.md](./README.md)** — user-facing overview (install/configure/run)

## 3. Current status

**Working (backtest path is complete):**
- LEAN integration (NuGet + thin launcher, C#/Python e2e)
- Data ETL — price (OHLCV), 수급 (investor flows), fundamentals (PER/PBR); per-ticker + universe bulk + daily incremental scheduler
- Orchestrator — LEAN Runner, SQLite persistence, FastAPI Control API, 3-chapter dashboard (전략 설정 / 백테스트 / 설정)
- Background-job backtests with live status/log
- Strategy framework (Alpha composition) + catalog (built-in EMA/MACD/RSI/momentum + custom 수급/저PBR)
- **Rule engine** — boolean conditions `(A AND B) OR C` (`/rules`)
- Universe selection (scan all loaded tickers)
- **Risk management** (global stop-loss/take-profit/trailing/portfolio)
- Config & secrets (env → config.local.yaml → dashboard `/settings`)

**Not done / gated:**
- ⛔ **Toss live trading** (`TossBrokerage`/`TossDataQueueHandler`, `MyTrading.Toss.dll`) — gated on Toss API (not open). Only piece needing the broker API.
- Minute-bar ETL (intraday strategies), OpenDART deep financials, news/sentiment, AI NL→strategy, universe criteria pre-filter, custom risk (ATR/vol), PCM selection, alerts, cross-platform packaging, LICENSE.

> See `README.md` 로드맵 for the up-to-date checklist.

> A local clone of [QuantConnect/Lean](https://github.com/QuantConnect/Lean) is a useful
> read-only reference (interfaces, sample data). Its path is machine-specific — never commit it.
