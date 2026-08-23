# Spider4AI 🕷️

**Agen trading crypto otonom pemburu memecoin Solana** — menggabungkan data on-chain/sosial dari [Cambrian API](https://cambrian.org), keputusan investasi dari **AI contract di blockchain GenLayer**, dan guardrail eksekusi lengkap (rug check → risk filter → sizing → cooldown → exit strategy).

> ⚠️ **Status: semi-production / edukasi.** Eksekusi default adalah DRY_RUN (paper trading). Belum ada swap mainnet. Gunakan wallet testnet saja.

---

## Apa yang dilakukan Spider4AI?

Setiap siklus, bot:

1. **Memindai pasar** — ambil token Solana paling aktif dari Cambrian (top-10 by volume), atau jalur khusus **meme hunter** (momentum X/Twitter ∩ trending).
2. **Menyaring yang bahaya** — rug checker memblokir token dengan pola scam (minting pasca-launch, konsentrasi holder, likuiditas ditarik).
3. **Menghitung skor** — scoring engine tier-aware berdasarkan FDV, volume, momentum, stabilitas harga.
4. **Bertanya ke AI di blockchain** — kirim payload ke intelligent contract GenLayer; kontrak menjalankan **3 LLM validator independen** (persona BULL/BEAR/NEUTRAL) dan memutuskan `BUY / WAIT / SKIP / SCAM` via weighted voting on-chain.
5. **Mengeksekusi dengan aman** — position sizing ketat, cooldown, circuit breaker, lalu exit otomatis (partial take-profit, crash exit, dev/whale exit).
6. **Melaporkan semuanya** — dashboard web real-time, report harian markdown, alert Telegram.

---

## Arsitektur

```
                        ┌─────────────────────────────────────────┐
                        │              CAMBRIAN API               │
                        │  trending · price · security · holders  │
                        │  mint/burn · social momentum · tweets   │
                        └───────────────────┬─────────────────────┘
                                            │ (cache 5 menit + budget guard)
┌───────────────┐   ┌───────────────────────▼───────────────────────┐
│ Web Dashboard │◄──│                SpiderAgent                    │
│  (FastAPI)    │   │                                               │
│  :8000        │   │  1. _fetch_markets      (main / meme hunter)  │
│               │   │  2. RugChecker          (hard-block scam)     │
│  Top Opps     │   │  3. RiskFilter          (tier-aware)          │
│  Watchlist    │   │  4. ScoringEngine       (tier-aware 0-100)    │
│  Meme Radar   │   │  5. ConfidenceScorer                          │
│  Alpha Radar  │   └──────────────────┬────────────────────────────┘
│  Action Log   │                      │ payload + onchain_context
└───────▲───────┘                      ▼
        │ event_sink        ┌─────────────────────────┐   gagal?
        │ (log real-time)   │    GenLayer Service     │────────────► TANPA keputusan
        └───────────────────│  intelligent contract   │              status "error"
                            │  BULL·BEAR·NEUTRAL LLM  │              opportunity
                            │  weighted voting on-chain│             dilewati
                            └────────────┬────────────┘   (tanpa fallback AI)
                                         │ BUY @ confidence ≥ 0.7
                                         ▼
                            ┌─────────────────────────┐
                            │     Trade Manager       │  sizing · cooldown
                            │  (+ SepoliaExecutor)    │  circuit breaker
                            └────────────┬────────────┘
                                         │ posisi OPEN
                                         ▼
                            ┌─────────────────────────┐
                            │    Exit Monitoring      │  partial TP · crash exit
                            │                         │  dev/whale exit
                            └─────────────────────────┘
                                         │ semua state
                                         ▼
                            ┌─────────────────────────┐
                            │    SQLite (WAL mode)    │  opportunities · positions
                            │                         │  blacklist · trade_events
                            └─────────────────────────┘  api_call_log
```

### Struktur folder

| Folder | Isi |
|---|---|
| `agents/` | Orkestrator pipeline utama (`SpiderAgent`) + event sink untuk UI |
| `data/` | Client Cambrian API (cache, retry, budget log), narrative detector |
| `engine/` | Rug checker, risk filter, scoring engine, confidence scorer, accumulation detector |
| `genlayer/` | Client SDK Bradbury, service layer GenLayer-only (tanpa fallback), source contract + primitive konsensus reusable |
| `execution/` | Trade manager (sizing/cooldown/breaker), exit strategies, Sepolia executor, DEX swap preview |
| `storage/` | SQLite persistence (WAL mode, dedup latest-per-symbol) |
| `web/` | Dashboard web FastAPI + HTML satu file |
| `ui/` | Dashboard terminal alternatif (Textual) |
| `reports/` | Generator report harian markdown (output tidak di-commit) |
| `notifications/` | Alert Telegram |
| `scripts/` | Deploy contract GenLayer + test live |

---

## Cara kerja pipeline

### 1. Sumber data — dua jalur

**Jalur utama (default):** trending tokens Solana top-10 by volume 24h → enrichment per token (detail harga, security, trend).

**Jalur meme hunter (`Scan Meme`):** dirancang hemat API — maksimal ~3 call tetap + beberapa call per kandidat:

1. Ambil **social momentum** X + **alpha tweets** → kumpulan simbol yang sedang ramai dibahas.
2. **Cross-match** simbol tersebut dengan trending top-100 (endpoint trending sudah membawa address mint) → mayoritas kandidat dapat address **tanpa lookup tambahan**.
3. Simbol yang tidak cocok dicari lewat **registry resolver yang di-cache RAM 6 jam** (sekali build ≈ 10 call, sisanya instan).
4. Filter **FDV band 10k–200k** (wilayah memecoin dini).
5. Lolos ke **4 gerbang anti-rug deterministik** (lihat bawah).

### 2. Gerbang anti-rug (sebelum AI menyentuh apa pun)

Token wash-trading bisa memalsukan volume — maka sebelum masuk pipeline AI, setiap kandidat meme wajib lolos:

| Gerbang | Default | Mendeteksi |
|---|---|---|
| Konsentrasi top-10 holder | ≤ 30% supply | pump-and-dump terkoordinasi |
| Jumlah holder minimum | ≥ 100 | token 5-holder yang mudah dimanipulasi |
| Rasio Volume/FDV | ≤ 50× | volume cuci (wash trading) |
| Uniqueness transaksi | ≥ 0.2 | self-trading antar sedikit wallet |

Semua threshold configurable via env (`SPIDER4AI_ALPHA_GATE_*`). Data security diambil dari endpoint `tokens/security` + `tokens/holders` Cambrian.

Di tahap berikutnya, **RugChecker** tetap hard-block bila ada: minting pasca-launch, top holder ≥ 20%, top-10 ≥ 50%, atau likuiditas ditarik — hasil block dicatat sebagai event `BLOCKED` di database.

### 3. Scoring tier-aware

FDV menentukan tier, tier menentukan bobot skor:

| Tier | Rentang FDV | Karakter skor |
|---|---|---|
| `alpha` | ≤ 200k | momentum & volume dominan |
| `low` | ≤ 5M | seimbang momentum + stabilitas |
| `mid` | ≤ 100M | seimbang |
| `big` | > 100M | stabilitas dominan (40%) |

Skor 0–100 + narrative detection (Layer2/AI/Meme/DeFi/dll) + confidence score.

### 4. Keputusan AI via GenLayer

Payload berisi symbol, summary, signal strength, risk flags, narrative, `onchain_context` (minting/konsentrasi/likuiditas) dikirim ke intelligent contract:

- **Network:** GenLayer Testnet **Bradbury** (chain id 4221, token GEN)
- **Contract:** [`0x54ba38e9D06cE4f99a3EA94A70101014C9ae261d`](https://explorer-bradbury.genlayer.com/) — source: `genlayer/contracts_src/trade_decision_contract.py`
- **Konsensus:** tiap persona (BULL/BEAR/NEUTRAL) dievaluasi lewat Equivalence Principle (pola leader–validator jaringan); agregasi akhir = **weighted voting asimetris di level contract**: BEAR 1.35× > NEUTRAL 1.15× > BULL 1.0× (downside diberi bobot lebih besar sesuai risiko trading).
- **Tie-break konservatif:** seri dimenangkan urutan `SCAM > SKIP > WAIT > BUY`.
- **Fail-closed:** satu persona mengembalikan JSON invalid → seluruh evaluasi revert tanpa partial commit.
- **GenLayer-only, tanpa fallback AI lokal (kebijakan desain):** jika GenLayer gagal/timeout setelah semua retry, siklus menghasilkan status `error` **tanpa keputusan** — opportunity ditandai `no_decision` dan dilewati. Bot tidak pernah memutuskan trade pakai model lokal atau heuristik. Banner debug: `[GENLAYER ACTIVE]` / `[GENLAYER DISABLED]`.

Catatan teknis platform: GenVM storage butuh tipe khusus (`DynArray`, `u256`, `TreeMap`) dan calldata **tidak mendukung float** → semua I/O dikirim sebagai JSON string.

#### Verifikasi live (Bradbury testnet)

| Jalur | Hasil | Bukti |
|---|---|---|
| Keputusan on-chain (3 persona + weighted voting) | ✅ `WAIT @ 0.67`, disagreement 0.43 | tx `0xb5f72b1c...d551f1ad`, konsensus jaringan: 5 validator (3 AGREE), `FINISHED_WITH_RETURN` |
| Pipeline penuh (payload → keputusan → eksekusi) | ✅ `deferred` untuk non-BUY | event log `[genlayer] E2E -> WAIT @ 0.57 (genlayer)` → `[exec] deferred` |
| Guard DRY_RUN | ✅ transaksi disimulasikan, tanpa broadcast | `0xSIMULATED_...11155111` |
| Degradasi saat API throttled | ✅ siklus dilewati tanpa crash | `[scan] Tidak ada data market dari Cambrian` |

### 5. Guardrail eksekusi

Keputusan `BUY` hanya dieksekusi bila:

- confidence ≥ `SPIDER4AI_MIN_TRADE_CONFIDENCE` (default 0.7)
- disagreement validator ≤ 0.45
- token tidak di blacklist, tidak kena block rug checker
- melewati sizing: 1–5% dari paper capital $10.000, cap $500/posisi
- cooldown global (180s) dan per-token (300s) tidak aktif
- circuit breaker tidak terpicu (3 loss berturut-turut → pause 30 menit)

`SPIDER4AI_DRY_RUN=true` (default): semua langkah di atas tetap dijalankan dan dicatat, tapi **tidak ada transaksi nyata**.

### 6. Exit otomatis

Posisi `OPEN` dimonitor tiap siklus, prioritas crash/dev di atas take-profit:

1. **Partial TP** — harga ≥ 2× entry (sekali saja): jual 50%, sisanya moonbag dengan trailing stop.
2. **Crash exit** — turun ≥ 25% dalam 15 menit: tutup semua + circuit breaker.
3. **Dev/whale exit** — wallet besar (tersimpan saat entry) menjual > 30% holdingnya: tutup semua.

Semua exit menghormati DRY_RUN dan mengirim notifikasi Telegram bila dikonfigurasi.

---

## Web dashboard

Cara tercepat memakai bot:

```bash
pip install -r requirements.txt
py -3.13 main.py web            # buka http://127.0.0.1:8000
```

| Panel | Isi |
|---|---|
| **Top Opportunities** | hasil scan utama: score, decision, confidence, sumber keputusan, FDV/mcap, waktu scan |
| **Watchlist** | token terpantau + status sistem |
| **Meme Radar** | hasil Scan Meme (jalur alpha hunter) |
| **System Status** | dry-run, contract, auto-scan, budget Cambrian hari ini vs bulanan |
| **Alpha Radar** | momentum X + alpha tweets (cache server 10 menit) |
| **Action Log** | jejak real-time setiap tahap agent (scan/risk/score/genlayer/exec) |

Tombol operasi: `Scan`, `🐕 Scan Meme`, toggle Auto-scan, Generate Report, Test Trade, toggle Dry-Run/Live.

REST API internal:

```
GET  /api/health  /api/status  /api/opportunities  /api/watchlist  /api/logs  /api/alpha
POST /api/scan  /api/scan-meme  /api/auto-scan  /api/report  /api/test-trade  /api/dry-run  /api/alpha/refresh
```

---

## Setup

```bash
# 1. Python 3.13+ (wajib untuk genlayer-py)
# 2. Install dependencies
pip install -r requirements.txt
pip install pytest

# 3. Konfigurasi
cp .env.txt .env    # lalu isi CAMBRIAN_API_KEY minimal

# 4. Jalankan
py -3.13 main.py web        # web dashboard (rekomendasi)
py -3.13 main.py            # dashboard terminal
py -3.13 main.py scan       # satu siklus scan di CLI
```

### Referensi konfigurasi (.env)

<details>
<summary><b>Semua variabel environment</b></summary>

```env
# === WAJIB ===
CAMBRIAN_API_KEY=your-key
SPIDER4AI_GENLAYER_ENABLED=true
SPIDER4AI_GENLAYER_CONTRACT_ADDRESS=0x54ba...
SPIDER4AI_SEPOLIA_RPC_URL=https://...
SPIDER4AI_WALLET_PRIVATE_KEY=0x...     # wallet TESTNET!

# === Mode & siklus ===
SPIDER4AI_DRY_RUN=true                 # JANGAN dimatikan kecuali paham risiko
SPIDER4AI_SCHEDULER_MINUTES=10         # interval auto-scan
SPIDER4AI_ALPHA_HUNTER_ENABLED=false   # true = jalur utama pakai meme hunter

# === Cambrian & budget ===
CAMBRIAN_BASE_URL=https://api.cambrian.org
CAMBRIAN_MONTHLY_BUDGET=1000           # plan gratis = 1000 req/bulan
CAMBRIAN_SAFETY_MARGIN=0.9             # threshold budget-saving = 900 call
CAMBRIAN_CACHE_TTL_SECONDS=300
CAMBRIAN_LIQUIDITY_VOLUME_DIVISOR=5    # proxy liquidity = vol24h / divisor

# === GenLayer ===
SPIDER4AI_GENLAYER_TIMEOUT_SECONDS=150 # voting consensus butuh waktu (≥120 disarankan)
SPIDER4AI_GENLAYER_MAX_RETRIES=3

# === Eksekusi & risiko ===
SPIDER4AI_MIN_TRADE_CONFIDENCE=0.7
SPIDER4AI_MAX_VALIDATOR_DISAGREEMENT=0.45
SPIDER4AI_PAPER_CAPITAL_USD=10000
SPIDER4AI_MIN_POSITION_PCT=0.01
SPIDER4AI_MAX_POSITION_PCT=0.05
SPIDER4AI_MAX_TRADE_SIZE_USD=500
SPIDER4AI_GLOBAL_COOLDOWN_SECONDS=180
SPIDER4AI_TOKEN_COOLDOWN_SECONDS=300
SPIDER4AI_CIRCUIT_BREAKER_MAX_LOSSES=3
SPIDER4AI_CIRCUIT_BREAKER_PAUSE_MINUTES=30

# === Tier FDV ===
SPIDER4AI_TIER_ALPHA_MAX_FDV=200000
SPIDER4AI_TIER_LOW_MAX_FDV=5000000
SPIDER4AI_TIER_MID_MAX_FDV=100000000

# === Meme hunter & gerbang anti-rug ===
SPIDER4AI_ALPHA_MIN_FDV=10000
SPIDER4AI_ALPHA_HUNTER_LIMIT=10
SPIDER4AI_ALPHA_GATE_TOP10_MAX_PCT=30
SPIDER4AI_ALPHA_GATE_MIN_HOLDERS=100
SPIDER4AI_ALPHA_GATE_MAX_VOLUME_FDV_RATIO=50
SPIDER4AI_ALPHA_GATE_MIN_TX_UNIQUENESS=0.2
SPIDER4AI_REGISTRY_CACHE_TTL_HOURS=6

# === Rug checker ===
SPIDER4AI_RUGCHECK_TOP_HOLDER_PCT=20
SPIDER4AI_RUGCHECK_TOP10_HOLDER_PCT=50
SPIDER4AI_RUGCHECK_MAX_HOLDERS=100

# === Exit strategy ===
SPIDER4AI_EXIT_TP_PARTIAL_MULTIPLIER=2.0
SPIDER4AI_EXIT_TP_PARTIAL_SELL_PCT=50
SPIDER4AI_EXIT_CRASH_DROP_PCT=25
SPIDER4AI_EXIT_CRASH_WINDOW_MINUTES=15
SPIDER4AI_EXIT_DEV_WHALE_SELL_PCT=30

# === Notifikasi (opsional) ===
SPIDER4AI_TELEGRAM_BOT_TOKEN=
SPIDER4AI_TELEGRAM_CHAT_ID=
```

</details>

---

## Perlindungan budget API

Plan gratis Cambrian = **1.000 request/bulan**. Perlindungan berlapis:

- **Log tiap panggilan** di tabel `api_call_log`; error 4xx tidak di-retry (hemat budget), 5xx/429 di-retry dengan backoff eksponensial.
- **Cache TTL 5 menit** per endpoint+token — cache hit gratis.
- **Budget-saving mode** otomatis di ≥90% budget: enrich per-token dilewati, hanya data trending dipakai + peringatan Telegram sekali.
- **Registry cache 6 jam** di RAM untuk resolusi simbol→address meme hunter.

```bash
python main.py cambrian-usage   # pemakaian hari ini / bulan ini / proyeksi habis
```

> Catatan: endpoint Cambrian tidak menyediakan TVL pool → bot memakai `volume24h ÷ 5` sebagai proxy likuiditas. Likuiditas 0 = cek dilewati, bukan token ditolak.

---

## Perintah CLI

```bash
py -3.13 main.py                  # dashboard terminal (default)
py -3.13 main.py web              # web dashboard :8000 (--host/--port bisa diatur)
py -3.13 main.py scan             # satu siklus scan
py -3.13 main.py healthcheck      # cek Cambrian + GenLayer + DB + .env
py -3.13 main.py status           # RPC, wallet, GenLayer snapshot
py -3.13 main.py db-check         # 10 opportunity terakhir
py -3.13 main.py cambrian-usage   # pemakaian & proyeksi budget
py -3.13 main.py report           # generate report harian markdown
py -3.13 main.py daily-report --schedule   # scheduler report 24 jam
py -3.13 main.py genlayer-test    # dummy payload ke GenLayer
py -3.13 main.py testtrade --yes  # simulasi transaksi Sepolia
py -3.13 main.py swap-test        # preview swap ETH->token (tanpa broadcast)
py -3.13 main.py reset-db --yes   # hapus database lokal
py -3.13 scripts/deploy_contract.py        # deploy contract baru
py -3.13 scripts/test_genlayer_live.py     # uji evaluate_trade live
```

---

## Database

SQLite tunggal (`spider4ai.db`, WAL mode + busy timeout 30s — aman diakses web server & job background bersamaan):

| Tabel | Isi |
|---|---|
| `opportunities` | riwayat hasil scan lengkap (append-only history; query UI mengambil entri terbaru per simbol) |
| `market_data`, `dex_data` | snapshot harga/volume/DEX |
| `blacklist_tokens` | token diblokir permanen |
| `positions`, `trade_events` | posisi terbuka & jejak eksekusi/exit |
| `api_call_log` | audit pemakaian Cambrian (dasar budget-saving mode) |

---

## Testing

```bash
py -3.13 -m pytest tests -q
```

Suite mencakup: pipeline SpiderAgent (termasuk event sink & gerbang anti-rug), tier scoring, risk filter, RugChecker, payload GenLayer & kebijakan tanpa-fallback, confidence scorer, circuit breaker, report harian, dedup & migrasi kolom database.

---

## Troubleshooting

| Gejala | Penyebab umum & solusi |
|---|---|
| Semua `decision_source = disabled` | `SPIDER4AI_GENLAYER_ENABLED=true` belum diset, alamat contract kosong, atau pakai Python < 3.13 |
| GenLayer timeout/retry | Voting consensus butuh waktu; naikkan `GENLAYER_TIMEOUT_SECONDS` ≥ 120 |
| Deploy gagal `FINISHED_WITH_ERROR` | Tipe storage salah (`list`/`int`/`dict`) atau float di calldata → pakai `DynArray`/`u256`/JSON string |
| Scan Meme hasil 0 + log "sumber sosial kosong" | Rate limit Cambrian (HTTP 429) atau pasar sepi; tunggu cooldown, cek `/api/status` |
| Tidak ada transaksi walau BUY | Cek DRY_RUN masih aktif, confidence < 0.7, blacklist/rug-block, disagreement tinggi, atau cooldown aktif |
| Port 8000 sudah dipakai | Server lama masih jalan — matikan dulu (`Get-Process python`), jangan jalankan dua instance (SQLite lock) |
| Test trade gagal | RPC/wallet belum benar, saldo Sepolia kosong, faucet: https://sepoliafaucet.com |

---

## Keamanan & disclaimer

- **Belum ada eksekusi mainnet.** Executor saat ini adalah bridge transaksi testnet Sepolia.
- Gunakan **wallet testnet** — jangan pernah isi private key wallet utama.
- `.env` berisi secret dan sudah di-`.gitignore`.
- Keputusan AI on-chain bukan nasihat keuangan. Memecoin adalah aset ekstrem risiko; gerbang anti-rug mengurangi — bukan menghilangkan — kemungkinan rug pull.

## Lisensi

Lihat repositori untuk detail lisensi.
