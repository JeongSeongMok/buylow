# Development

> How to set up, build, and run buylow locally. For the system design see
> [ARCHITECTURE.md](./ARCHITECTURE.md).

## Prerequisites

- **.NET 10 SDK** (LEAN targets `net10.0`)
- **Python 3.11** — LEAN's `pythonnet` runtime uses 3.11 specifically
- **[uv](https://github.com/astral-sh/uv)** — Python env/dependency manager
- **git**
- For the backtest smoke test: a folder of **LEAN-format market data** (see below)

### Installing .NET 10 without sudo

The official script installs into `~/.dotnet` without touching the system:

```bash
curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 10.0 --install-dir "$HOME/.dotnet"
export DOTNET_ROOT="$HOME/.dotnet"
export PATH="$HOME/.dotnet:$PATH"
```

(`scripts/run-backtest.sh` sets these automatically if `~/.dotnet` is used.)

## LEAN NuGet version trap (important)

QuantConnect publishes **two lineages** on NuGet, and the higher semver is the **old** one:

| Lineage | Example | Target | Use? |
|---|---|---|---|
| `2.5.x` (current) | **`2.5.17757`** | `net10.0` | ✅ use this |
| `10730.0.0` (legacy) | `10730.0.0` | `net462` | ❌ incompatible with net10 |

`versions[-1]` resolves to the legacy `10730.x` — do **not** use it. All core packages
(`QuantConnect.Lean.Engine`, `Common`, `Brokerages`, `Indicators`, `Algorithm`,
`Compression`, `Configuration`, `Messaging`, `Queues`, `Api`, `Research`) are at
`2.5.17757`. Note: the `Holding` type lives in the root `QuantConnect` namespace.

## Building the launcher

```bash
dotnet build launcher/BuylowLauncher.csproj -c Release
```

`launcher/` is a net10 console app that vendors LEAN's `Program.cs` and references the
LEAN Engine NuGet (no fork). LEAN's `Composer` loads handler/plugin assemblies by name
from the output folder; `launcher/config.json` selects them.

## Running the backtest smoke test

A health check that the LEAN integration works end-to-end (no Toss/live needed):

```bash
# Point at a folder of LEAN-format data (e.g. the Data/ dir of a local QuantConnect/Lean clone).
export LEAN_DATA_DIR=/path/to/lean/Data
./scripts/run-backtest.sh
```

- First run creates a Python 3.11 venv at `.leanpy/` (with pandas/numpy) automatically.
- **Exit code 0 means the LEAN integration is healthy.**
- Run a different strategy:
  ```bash
  STRATEGY=strategies/MyStrategy.py ALGO_TYPE=MyStrategy ./scripts/run-backtest.sh
  ```

### How the run script wires Python

LEAN's Python strategies do `from AlgorithmImports import *`, which loads many
`QuantConnect.*` CLR assemblies. The script sets:

- `PYTHONNET_PYDLL` → the detected `libpython3.11` shared library
- `PYTHONPATH` → the `.leanpy` site-packages, the `AlgorithmImports.py` directory (shipped
  inside the `QuantConnect.Common` NuGet package's `content/`), and the strategy directory

## Running via the orchestrator (LEAN Runner)

The orchestrator's `LeanRunner` (`orchestrator/lean/`) is the programmatic equivalent of
`run-backtest.sh`: it resolves the environment, builds the launcher, generates `config.json`,
spawns the LEAN process, and parses the results.

```bash
LEAN_DATA_DIR=/path/to/lean/Data python -m orchestrator.lean
# a different strategy + parameters:
LEAN_DATA_DIR=/path/to/lean/Data python -m orchestrator.lean \
    --strategy strategies/My.py --algo-type My --param threshold=0.12
```

Results land in `runs/<run-id>/` (the LEAN result JSON + `run.log`); summary statistics are
parsed from stdout. Exit code `0` = the backtest completed. This is the same machinery the
dashboard/API will call later. (`scripts/run-backtest.sh` remains as a no-Python shell check.)

## Control API (dashboard backend)

```bash
# one-time: create a dev venv and install the orchestrator + dev deps
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# run the Control API on 127.0.0.1:8420 (port via BUYLOW_DASHBOARD_PORT)
LEAN_DATA_DIR=/path/to/lean/Data .venv/bin/python -m orchestrator.api
```

Endpoints: `GET /healthz`, `POST /runs` (trigger a backtest), `GET /runs`, `GET /runs/{id}`.
State is persisted in `buylow.db` (SQLite, gitignored). The server binds to `127.0.0.1` only.

## Tests

```bash
.venv/bin/python -m pytest            # fast unit + API tests
# end-to-end backtest (needs .NET + Python 3.11 + LEAN_DATA_DIR):
LEAN_DATA_DIR=/path/to/lean/Data .venv/bin/python -m pytest -m integration -o addopts=""
```

Per project rule, every feature ships with tests. Integration tests (real LEAN runs) are
marked `@pytest.mark.integration` and deselected by default so the normal run stays fast.

## Machine-specific notes / gotchas

- **`dotnet` not on PATH:** export `DOTNET_ROOT`/`PATH` as above (the run script does this for `~/.dotnet`).
- **git commit fails with `invalid value for 'gpg.format': ''`:** your global `~/.gitconfig`
  has an empty `format =` under `[gpg]`. Remove that line (reverts to git's default). Commit
  signing is unaffected if `commit.gpgsign = false`.
- **A local clone of [QuantConnect/Lean](https://github.com/QuantConnect/Lean)** is a useful
  read-only reference for interfaces and sample data; its path is machine-specific (set
  `LEAN_DATA_DIR` to its `Data/` for the smoke test).

## Validation log

**2026-05-30** — Verified end-to-end on net10:
- .NET 10 SDK + LEAN `2.5.17757` packages restore and build.
- A net10 class library references LEAN, derives from `Brokerage`, implements
  `IDataQueueHandler`, and builds (`MyTrading.Toss.dll`).
- A self-built net10 thin launcher runs a full backtest (C# and Python strategies, identical results).
- Python strategy execution works via pythonnet (Python 3.11 + pandas + `AlgorithmImports`).
- `Composer` loads assemblies by name from the output folder.
