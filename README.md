# Spider4AI

Spider4AI is an autonomous crypto market hunter agent focused on **mid-cap opportunity discovery**. It scans markets, detects narratives, identifies accumulation signals, ranks coins with a conviction model, and supports optional Sepolia testnet transaction simulation.

> Safety first: Spider4AI does **not** perform real trading. The execution module is testnet-only.

## Features

- Market scanner for mid-cap assets (CoinGecko)
- DEX radar for trending token liquidity/volume (Dexscreener)
- Narrative detection (Ollama-compatible with keyword fallback)
- Accumulation signal detection
- 0–100 conviction scoring engine
- Risk filter for suspicious assets
- Real-time terminal dashboard (Textual + Rich)
- Daily report generator
- Optional Sepolia test transaction simulation (web3.py)

## Installation

1. Ensure Python 3.11 is installed.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Set environment variables as needed:

- `SPIDER4AI_DB_PATH` (default `spider4ai.db`)
- `SPIDER4AI_OLLAMA_URL` (default `http://localhost:11434`)
- `SPIDER4AI_OLLAMA_MODEL` (default `llama3`)
- `SPIDER4AI_SEPOLIA_RPC_URL` (required for `testtrade`)
- `SPIDER4AI_WALLET_PRIVATE_KEY` (required for `testtrade`)

## Usage

Run market scan:

```bash
python main.py scan
```

Start dashboard (or just run `python main.py`):

```bash
python main.py dashboard
```

Dashboard hotkeys:
- `S` run scan now
- `A` toggle auto-scan scheduler
- `R` generate report
- `T` run Sepolia test transaction
- `Q` quit

Generate report:

```bash
python main.py report
```

Simulate Sepolia transaction:

```bash
python main.py testtrade
```


## Dashboard-First Quick Start

The recommended way to operate Spider4AI is the integrated dashboard:

```bash
python main.py
```

Use hotkeys inside the dashboard:
- `S` run scan now
- `A` toggle auto-scan scheduler
- `R` generate report
- `T` run Sepolia test transaction
- `Q` quit

## Spider Agent Loop

The `SpiderAgent` module encapsulates the complete workflow:

1. Scan markets
2. Update database
3. Detect narratives
4. Run accumulation analysis
5. Compute conviction scores
6. Filter risk
7. Save ranked opportunities

A background scheduler helper is included to run this loop every 10 minutes.
