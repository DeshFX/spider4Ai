# Spider4AI

Spider4AI adalah agen AI crypto trading semi-production yang:
- scan market lewat **Cambrian API** (satu-satunya sumber data eksternal)
- enrich dengan liquidity / narrative / onchain data
- menilai risiko **rugpull** (RugChecker) sebelum masuk pipeline
- mengklasifikasikan token ke **tier FDV** (alpha/low/mid/big) untuk scoring yang berbeda
- mengirim payload ke **GenLayer intelligent contract** (testnet Bradbury) bila aktif
- fallback ke local AI / heuristic bila GenLayer gagal
- menerapkan guardrail eksekusi, sizing, cooldown, dan exit monitoring.

## Arsitektur

- `agents/` → orchestration pipeline utama (`SpiderAgent`), termasuk mode alpha hunter
- `data/` → `cambrian_client.py` (client Cambrian API: harga, risiko, sentiment, trending, alpha tweets)
- `engine/` → `scoring_engine.py` (tier-aware), `risk_filter.py`, `rug_checker.py`, `confidence_scorer.py`
- `genlayer/` → client (Bradbury), contract adapter, fallback, decision transport, source contract
- `execution/` → safety layer, sizing, cooldown, position management, exit strategies (partial TP, crash exit, dev/whale exit), Sepolia test executor
- `storage/` → SQLite persistence untuk opportunities, blacklist, positions, trade events, api_call_log
- `ui/` → dashboard terminal Textual
- `reports/` → generator report harian markdown
- `notifications/` → alert Telegram (take profit, crash exit, budget warning)

## Pipeline scan (SpiderAgent)

1. Ambil **trending tokens** dari Cambrian (10 terbesar by volume) + detail enrichment.
2. Bila `SPIDER4AI_ALPHA_HUNTER_ENABLED=true`: cari **alpha candidates** (momentum + alpha tweets, FDV 10k–200k).
3. Klasifikasi tier FDV: `alpha` (≤200k) / `low` (≤5M) / `mid` (≤100M) / `big` (>100M).
4. **RugChecker** (hard-block Layer-1): block bila ada minting pasca-launch, top holder ≥20%, top10 ≥50%, atau likuiditas ditarik. Hasil hard-block masuk DB sebagai event `BLOCKED`.
5. Filter risiko tier-aware (`risk_filter.is_safe`) dan scoring tier-aware (`scoring_engine.score`).
6. Build decision payload termasuk `onchain_context` + `tier`, kirim ke GenLayer.
7. GenLayer meminta 3 validator LLM (BULL / BEAR / NEUTRAL) melakukan voting via consensus.
8. Simpan decision, confidence, disagreement, dan tx hash ke database.

## GenLayer intelligent contract

- **Network:** Testnet **Bradbury** (chain id 4221, token GEN)
- **Deployed contract:** `0x22D7BE081220B70b8f91f7c2fa2bD6CB00DCcf1B`
- **Explorer:** https://explorer-bradbury.genlayer.com/
- **SDK:** `genlayer-py` (butuh **Python 3.13+**; di Python 3.11 hanya ada package placeholder 0.0.1)
- **Faucet:** https://testnet-faucet.genlayer.foundation

Source contract: `genlayer/contracts_src/trade_decision_contract.py`

Catatan penting:
- GenVM storage butuh tipe khusus: `DynArray`, `u256`, `@allow_storage` (bukan `list`/`int`/`dict`).
- Calldata GenVM **tidak mendukung `float`** → semua input/output publik dikirim sebagai **JSON string**.
- Contract mengevaluasi `onchain_context` (minting, konsentrasi holder, likuiditas) — kalau menunjukkan pola scam, validator cenderung memutuskan `SCAM`.

### Deploy & test live

```bash
# deploy contract (Python 3.13)
py -3.13 scripts\deploy_contract.py

# test evaluate_trade live dengan payload contoh
py -3.13 scripts\test_genlayer_live.py
```

Script deploy mencetak alamat contract baru; set ke `SPIDER4AI_GENLAYER_CONTRACT_ADDRESS`.

## Setup cepat (.env based)

1. Gunakan **Python 3.13+** (wajib untuk genlayer-py).
2. Install dependency:
   - `pip install -r requirements.txt`
   - `pip install pytest` (untuk test suite)
3. Salin `.env.txt` → `.env` lalu sesuaikan (API key Cambrian, wallet, dst).

Variabel penting:

```env
CAMBRIAN_BASE_URL=https://api.cambrian.org
CAMBRIAN_API_KEY=your-cambrian-api-key
CAMBRIAN_MONTHLY_BUDGET=1000
CAMBRIAN_SAFETY_MARGIN=0.9
CAMBRIAN_CACHE_TTL_SECONDS=300
CAMBRIAN_LIQUIDITY_VOLUME_DIVISOR=5
SPIDER4AI_GENLAYER_ENABLED=true
SPIDER4AI_GENLAYER_CONTRACT_ADDRESS=0x22D7BE081220B70b8f91f7c2fa2bD6CB00DCcf1B
SPIDER4AI_WALLET_PRIVATE_KEY=0x...
SPIDER4AI_DRY_RUN=true
SPIDER4AI_ALPHA_HUNTER_ENABLED=false
SPIDER4AI_TIER_ALPHA_MAX_FDV=200000
SPIDER4AI_TIER_LOW_MAX_FDV=5000000
SPIDER4AI_TIER_MID_MAX_FDV=100000000
SPIDER4AI_RUGCHECK_TOP_HOLDER_PCT=20
SPIDER4AI_RUGCHECK_TOP10_HOLDER_PCT=50
SPIDER4AI_GENLAYER_TIMEOUT_SECONDS=150
SPIDER4AI_TELEGRAM_BOT_TOKEN=   # opsional, untuk notifikasi Telegram
SPIDER4AI_TELEGRAM_CHAT_ID=
SPIDER4AI_EXIT_CRASH_DROP_PCT=25
SPIDER4AI_EXIT_CRASH_WINDOW_MINUTES=15
SPIDER4AI_EXIT_DEV_WHALE_SELL_PCT=30
SPIDER4AI_EXIT_TP_PARTIAL_MULTIPLIER=2.0
SPIDER4AI_EXIT_TP_PARTIAL_SELL_PCT=50
```

Project memakai `python-dotenv`, jadi `.env` otomatis dibaca saat startup.

## Budget API Cambrian

Plan gratis Cambrian = **1000 request/bulan**. Spider4AI melindungi budget ini:

- **Counter + log:** setiap panggilan tercatat di tabel `api_call_log` (timestamp, endpoint, response_status). Error HTTP 4xx (mis. 400) **tidak di-retry** agar tidak membuang budget — hanya 5xx/429 yang di-retry dengan backoff.
- **Cache TTL 5 menit** per token+endpoint (`CAMBRIAN_CACHE_TTL_SECONDS`) untuk data yang jarang berubah (harga, metadata token, holders, security). Cache hit tidak menghabiskan budget.
- **Budget-saving mode:** saat pemakaian bulan ini ≥ `CAMBRIAN_MONTHLY_BUDGET × CAMBRIAN_SAFETY_MARGIN`, bot otomatis berhenti melakukan enrich per-token (pakai data trending saja) dan kirim peringatan Telegram sekali.

> **Liquidity (penting):** endpoint Cambrian tidak menyediakan TVL/liquidity (pool-search hanya berisi volume). Bot memakai **volume 24h ÷ `CAMBRIAN_LIQUIDITY_VOLUME_DIVISOR` (default 5)** sebagai proxy liquidity untuk risk filter & scoring. Saat data tidak tersedia (liquidity = 0), cek liquidity di-skip, bukan langsung menolak token.

Cek pemakaian kapan saja:

```bash
python main.py cambrian-usage
```

## GenLayer vs fallback

- Jika `SPIDER4AI_GENLAYER_ENABLED=true` dan contract address valid, sistem mencoba GenLayer.
- Jika gagal/timeout, sistem fallback ke local AI, lalu heuristic.
- Debug banner: `[GENLAYER ACTIVE]` / `[FALLBACK MODE]`.

## Tier FDV & scoring

| Tier | Rentang FDV | Fokus |
|------|-------------|-------|
| alpha | ≤ 200k | momentum/volume tinggi, stability kecil |
| low | ≤ 5M | seimbang momentum + stability |
| mid | ≤ 100M | seimbang |
| big | > 100M | stability lebih dominan (40) |

## DRY_RUN

`SPIDER4AI_DRY_RUN=true` adalah mode aman default.
Dalam mode ini:
- trade tetap diputuskan
- position plan tetap dibuat
- database tetap diupdate
- tetapi transaksi nyata tidak dikirim.

Set `SPIDER4AI_DRY_RUN=false` hanya jika kamu benar-benar ingin melewati bridge eksekusi testnet.

## Exit strategy otomatis

Setiap siklus monitoring, posisi `OPEN` dicek untuk 3 trigger (prioritas crash/dev > take-profit):

1. **Take profit bertahap** — harga ≥ `SPIDER4AI_EXIT_TP_PARTIAL_MULTIPLIER` (2x entry, sekali saja): jual 50% (`SPIDER4AI_EXIT_TP_PARTIAL_SELL_PCT`), sisanya jadi moonbag. Sisa posisi tetap dipantau oleh stop-loss / trailing-stop (static take-profit dimatikan setelah partial).
2. **Market crash exit** — harga turun ≥ `SPIDER4AI_EXIT_CRASH_DROP_PCT` (25%) dalam `SPIDER4AI_EXIT_CRASH_WINDOW_MINUTES` (15 menit): tutup seluruh posisi, aktifkan circuit breaker.
3. **Dev/whale exit** — dev wallet / top holder (≥ `RUGCHECK_TOP_HOLDER_PCT`) yang tersimpan saat posisi dibuka menjual > `SPIDER4AI_EXIT_DEV_WHALE_SELL_PCT` (30%) holding: tutup seluruh posisi.

Semua exit menghormati `SPIDER4AI_DRY_RUN` (simulasi + catat DB, tanpa broadcast) dan mengirim notifikasi Telegram bila dikonfigurasi.

## Commands

```bash
python main.py                    # dashboard default
python main.py scan               # run scan
python main.py agent-run          # full pipeline
python main.py genlayer-test      # kirim dummy payload ke GenLayer/fallback
python main.py db-check           # cek 10 opportunity terakhir
python main.py status             # status config / rpc / wallet / genlayer
python main.py healthcheck        # cek Cambrian API, GenLayer, database, .env
python main.py cambrian-usage     # pemakaian API Cambrian (hari/bulan/proyeksi limit)
python main.py report             # generate report markdown
python main.py daily-report       # generate / schedule report harian (--schedule)
python main.py testtrade --yes    # test Sepolia tx
python main.py swap-test          # preview swap Sepolia (tanpa broadcast)
python main.py reset-db --yes     # hapus database lokal
```

## Dashboard

Dashboard menampilkan:
- symbol coin
- decision (`BUY / WAIT / SKIP / SCAM`)
- confidence
- decision source (`genlayer / local_ai / heuristic / disabled`)
- status sistem, watchlist, dan log action.

## Tests

```bash
py -3.13 -m pytest tests -q
```

Suite mencakup: pipeline SpiderAgent, tier scoring & config, risk filter tier-aware, RugChecker, GenLayer payload/onchain, confidence scorer, circuit breaker, report harian.

## Troubleshooting

### Semua decision_source = disabled
Pastikan memakai:
- `SPIDER4AI_GENLAYER_ENABLED=true`
- `SPIDER4AI_GENLAYER_CONTRACT_ADDRESS` terisi (alamat contract yang sudah di-deploy)
- `py -3.13` (genlayer-py tidak tersedia di Python 3.11)

### GenLayer timeout / retry
Voting consensus butuh waktu (LLM validators). Naikkan `SPIDER4AI_GENLAYER_TIMEOUT_SECONDS` (default 20, disarankan ≥120 di testnet).

### Contract deployment gagal dengan FINISHED_WITH_ERROR
Cek trace via `debug_trace_transaction` di script deploy. Penyebab umum: tipe storage salah (`list`/`dict`/`int`), atau float di calldata → pakai `DynArray`/`TreeMap`/`u256` dan JSON string.

### Test trade gagal
Cek:
- `SPIDER4AI_SEPOLIA_RPC_URL`
- `SPIDER4AI_WALLET_PRIVATE_KEY`
- saldo Sepolia ETH
- RPC benar-benar mengarah ke Sepolia

### Tidak ada transaksi walau BUY
Kemungkinan:
- `SPIDER4AI_DRY_RUN=true`
- confidence < ambang
- token masuk blacklist / rugcheck block
- disagreement validator terlalu tinggi
- cooldown masih aktif

## Catatan keamanan

Spider4AI belum membeli token scan secara real. Executor Sepolia saat ini tetap bersifat test transaction bridge.
Gunakan wallet testnet dan jangan pakai private key wallet utama.
`.env` berisi secret (API key + private key) dan sudah masuk `.gitignore`.
