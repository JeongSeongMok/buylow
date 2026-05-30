<div align="center">

# buylow

**Automated algorithmic trading for Korean equities (KOSPI/KOSDAQ), built on [QuantConnect LEAN](https://github.com/QuantConnect/Lean).**

Write a strategy once and run it in both **backtest** and **live** trading.

English · [한국어](./README.ko.md) · [日本語](./README.ja.md)

</div>

---

> ⚠️ **Status: early development.** Backtest integration works today; Korean market data and
> Toss Securities live trading are in progress. **Not ready for real trading.**

## Overview

buylow uses the LEAN engine as a platform: a long-running Python orchestrator runs LEAN
(.NET) processes per task, and a Korea/Toss adapter plugs into LEAN for market definition
and live trading. Strategies are plain Python files, so the *same code* you backtest is what
trades live. See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the full design.

## Features

- Run trading strategies (e.g. BNF mean-reversion, trend-following) on the LEAN engine
- Backtest and live trading from the **same** strategy code
- **Bring your own API keys** (Toss, AI) — nothing is stored in the repo
- _Planned:_ strategy selection & scheduling, AI natural-language strategy generation, dashboard

## Prerequisites

- [.NET 10 SDK](https://dotnet.microsoft.com/download)
- [Python 3.11](https://www.python.org/) and [uv](https://github.com/astral-sh/uv)
- git

## Installation

```bash
git clone https://github.com/JeongSeongMok/buylow.git
cd buylow
# setup script: TBD
```

## Configuration

You provide your own API keys (Toss, AI) in a local, **gitignored** config. No keys are ever
committed. _(The exact config file is TBD as the live integration lands.)_

## Usage

Currently available: a **LEAN backtest smoke test** that verifies the engine integration
end-to-end.

```bash
# Point at a folder of LEAN-format market data
export LEAN_DATA_DIR=/path/to/lean/Data
./scripts/run-backtest.sh
```

Exit code `0` means the integration is healthy. See [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md)
for details.

## Roadmap

- [x] LEAN integration (backtest, C# + Python)
- [ ] KRX market definition (hours, KRW, fees/taxes)
- [ ] Korean historical data ETL (KRX → LEAN format)
- [ ] Toss Securities live trading adapter
- [ ] Orchestrator: scheduling, persistence, dashboard, alerts

## Documentation

- [Architecture](./docs/ARCHITECTURE.md) — system design and rationale
- [Development](./docs/DEVELOPMENT.md) — setup, build, and run
- [Agent guide](./CLAUDE.md) — conventions for AI-assisted development

## Disclaimer

This software is provided for educational purposes. Automated trading carries significant
financial risk; **use at your own risk**. The authors are not responsible for any financial
losses. Ensure your usage complies with your broker's API terms of service and all applicable
laws and regulations.

## License

TBD.
