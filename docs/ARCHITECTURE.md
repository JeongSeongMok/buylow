# Architecture

> System design and the reasoning behind it. For setup/build instructions see
> [DEVELOPMENT.md](./DEVELOPMENT.md); for the user-facing overview see the [README](../README.md).

## Goal

buylow is a personal automated algorithmic trading toolkit for Korean equities
(KOSPI/KOSDAQ), built on top of the QuantConnect **LEAN** engine. Its core value is
LEAN's **backtest–live isomorphism**: the *same strategy code* runs in backtesting and
in live trading.

Brokerage connectivity (orders, fills, real-time quotes) targets the **Toss Securities
API** (a later milestone).

## Distribution model

Open source, **clone-and-run** (no prebuilt installer):

```
git clone → install prerequisites (.NET 10, Python 3.11, uv) → setup script
          → put your own API keys (Toss, AI) in a local, gitignored config
          → run
```

- **No secrets in the repo.** Every user supplies their own keys (BYO-key).
- The repo stays source-only; we ship documentation + setup scripts, not binaries.

## Two-process model

A long-running **Python orchestrator** spawns short-lived **LEAN processes**, one per task.

```
┌─────────────────────────────────────────────────────────────┐
│ buylow orchestrator (Python / FastAPI)        ← always on     │
│   control API · scheduler · strategy mgmt · DB · dashboard    │
└───────────────┬─────────────────────────────────────────────┘
                │ spawn a subprocess per job / collect results & logs
                ▼
┌─────────────────────────────────────────────────────────────┐
│ LEAN process (.NET 10, "one process = one job")               │
│   ├ thin launcher (we build: LEAN Program.cs + Engine NuGet)  │
│   ├ strategy (Algorithm)         ← Python (pythonnet)         │
│   └ MyTrading.Toss.dll           ← our Korea/Toss adapter     │
│        · TossBrokerage : IBrokerage        (orders/fills)     │
│        · TossDataQueueHandler : IDataQueueHandler (quotes)    │
│        · Market.Add("krx") + KRW + Korean fees/taxes          │
└─────────────────────────────────────────────────────────────┘
```

- **Backtest / optimization**: spawn per job, exit when done (short-lived).
- **Live**: one long-lived process per strategy (N strategies = N processes).
- The orchestrator is the only always-on component; it spawns/monitors/kills/restarts
  LEAN processes.

## What LEAN owns vs. what we build

LEAN is **not** modified or forked. We reference it via NuGet and extend it through
documented interfaces ("seams").

**LEAN owns (the verified core we reuse):**
- Event loop & time synchronization (`AlgorithmManager`) — the basis of backtest–live isomorphism
- Data management: feeds, subscriptions, resolution, time zones, corporate-action adjustment
- Strategy API (`QCAlgorithm`): `add_equity`, `set_holdings`, indicators, scheduling, universe selection, history
- Portfolio / order / fill / fee / slippage / buying-power models
- Indicator library and the 5-stage strategy framework
- Market-hours and symbol-properties databases; statistics & reporting

**We build (the seams + everything around LEAN):**

| Seam | Interface | Our implementation |
|---|---|---|
| Live orders/fills | `IBrokerage` | `TossBrokerage` |
| Live quotes | `IDataQueueHandler` | `TossDataQueueHandler` |
| Market definition | `Market.Add`, market-hours / symbol-properties JSON | KRX (09:00–15:30, holidays, KRW) |
| Cost model | `FeeModel` | Korean commissions + transaction tax |
| Historical data | LEAN data format (zip+csv) | KRX → LEAN ETL |

Plus the **orchestrator** (process lifecycle, scheduling, persistence, control API,
dashboard, alerts) and a **thin launcher** (below).

## C# artifacts (two)

1. **Thin launcher** — a net10 console app that vendors LEAN's `Launcher/Program.cs`
   (Apache-2.0) verbatim and references the LEAN Engine NuGet. We build our own because
   the published `QuantConnect.Lean.Launcher` NuGet targets net462 and is unusable on
   net10. The engine logic stays untouched in the NuGet package; only the entry point is ours.
2. **`MyTrading.Toss.dll`** — the Korea/Toss adapter (brokerage, data queue, market definition, fees).

The runtime loads plugin assemblies by name (LEAN's `Composer` scans the output folder;
`config.json` names them), which is how our adapter plugs in without modifying LEAN.

## Data flow & strategy framework

```
raw data → Slice → strategy → Insight → PortfolioTarget → Order → fill → portfolio → statistics
```

The 5-stage framework (the design basis for strategies):
**Universe Selection → Alpha (Insight) → Portfolio Construction (PortfolioTarget) →
Risk Management → Execution (Order).**

## Pipelines

- **Backtest** (working): strategy + historical data → thin launcher runs LEAN → statistics.
  Reproducible via `scripts/run-backtest.sh`.
- **Live** (planned): `TossDataQueueHandler` → strategy → `TossBrokerage` → fill reconciliation.

## Roadmap

1. **KRX market definition** — `Market.Add("krx")`, market hours, KRW settlement, 6-digit code ↔ Symbol, Korean fee/tax model
2. **Korean historical data ETL** — KRX/vendor → LEAN format (precompute universe indicators where useful)
3. **Backtest validation** with Korean data
4. **Toss live integration** — `IBrokerage` / `IDataQueueHandler`
5. **Server features** — orchestration, scheduling, persistence, dashboard, alerts, operational safety
