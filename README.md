# Spider4AI

Spider4AI adalah agen AI crypto semi-production yang:
- scan market data
- enrich dengan liquidity / narrative
- kirim payload ke GenLayer bila aktif
- fallback ke local AI / heuristic bila GenLayer gagal
- menerapkan guardrail eksekusi, sizing, cooldown, dan exit monitoring.

## Arsitektur

- `agents/` → orchestration pipeline utama (`SpiderAgent`)
- `genlayer/` → client, contract adapter, fallback, decision transport
- `execution/` → safety layer, sizing, cooldown, position management, Sepolia test executor
- `storage/` → SQLite persistence untuk opportunities, blacklist, positions, trade events
- `ui/` → dashboard terminal Textual

## Setup cepat (.env based)

1. Pastikan Python 3.11 tersedia.
2. Buat virtualenv.
3. Install dependency:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Buat file `.env` di root project:

```env
SPIDER4AI_DB_PATH=spider4ai.db
SPIDER4AI_OLLAMA_URL=http://localhost:11434
SPIDER4AI_OLLAMA_MODEL=llama3
SPIDER4AI_GENLAYER_ENABLED=false
SPIDER4AI_GENLAYER_CONTRACT_ADDRESS=
SPIDER4AI_SEPOLIA_RPC_URL=
SPIDER4AI_WALLET_PRIVATE_KEY=
SPIDER4AI_DRY_RUN=true
SPIDER4AI_MAX_TRADE_SIZE_USD=500
```

Project sekarang memakai `python-dotenv`, jadi `.env` akan otomatis dibaca saat startup.

## GenLayer vs fallback

- Jika `SPIDER4AI_GENLAYER_ENABLED=true` dan contract address valid, system akan mencoba GenLayer.
- Jika gagal, system fallback ke local AI, lalu ke heuristic.
- Debug banner akan muncul sebagai:
  - `[GENLAYER ACTIVE]`
  - `[FALLBACK MODE]`

## DRY_RUN

`SPIDER4AI_DRY_RUN=true` adalah mode aman default.
Dalam mode ini:
- trade tetap diputuskan
- position plan tetap dibuat
- database tetap diupdate
- tetapi transaksi nyata tidak dikirim.

Set `SPIDER4AI_DRY_RUN=false` hanya jika kamu benar-benar ingin melewati bridge eksekusi testnet.

## Commands

```bash
python main.py                 # dashboard default
python main.py scan            # run scan
python main.py agent-run       # full pipeline
python main.py genlayer-test   # kirim dummy payload ke GenLayer/fallback
python main.py db-check        # cek 10 opportunity terakhir
python main.py status          # status config / rpc / wallet / genlayer
python main.py report          # generate report markdown
python main.py testtrade --yes # test Sepolia tx
python main.py reset-db --yes  # hapus database lokal
```

## Dashboard

Dashboard menampilkan:
- symbol coin
- decision (`BUY / WAIT / SCAM`)
- confidence
- decision source (`genlayer / local_ai / heuristic / disabled`)
- status sistem, watchlist, dan log action.

## Troubleshooting

### Semua decision_source = disabled
Pastikan memakai:
- `SPIDER4AI_GENLAYER_ENABLED=true`
- bukan `SPIDER4AI_ENABLE_GENLAYER` saja (alias lama masih didukung)

### Test trade gagal
Cek:
- `SPIDER4AI_SEPOLIA_RPC_URL`
- `SPIDER4AI_WALLET_PRIVATE_KEY`
- saldo Sepolia ETH
- RPC benar-benar mengarah ke Sepolia

### Tidak ada transaksi walau BUY
Kemungkinan:
- `SPIDER4AI_DRY_RUN=true`
- confidence < 0.7
- token masuk blacklist
- disagreement validator terlalu tinggi
- cooldown masih aktif

## Catatan keamanan

Spider4AI belum membeli token scan secara real. Executor Sepolia saat ini tetap bersifat test transaction bridge.
Gunakan wallet testnet dan jangan pakai private key wallet utama.
