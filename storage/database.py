"""SQLite storage layer for Spider4AI market intelligence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterable
from pathlib import Path

from config import settings


class Database:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_id TEXT,
                    symbol TEXT,
                    name TEXT,
                    price REAL,
                    volume_24h REAL,
                    market_cap REAL,
                    change_24h REAL,
                    fetched_at TEXT
                );
                CREATE TABLE IF NOT EXISTS dex_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    pair_address TEXT,
                    dex_id TEXT,
                    liquidity REAL,
                    volume_24h REAL,
                    price_usd REAL,
                    fetched_at TEXT
                );
                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_id TEXT,
                    symbol TEXT,
                    narrative TEXT,
                    score REAL,
                    accumulation_score REAL,
                    volume_24h REAL,
                    liquidity REAL,
                    price REAL,
                    reason TEXT,
                    summary TEXT,
                    risk_flags TEXT,
                    signal_strength REAL,
                    source TEXT,
                    market_context TEXT,
                    recent_trend TEXT,
                    market_stability REAL,
                    genlayer_status TEXT,
                    genlayer_decision TEXT,
                    genlayer_confidence REAL,
                    genlayer_reasoning TEXT,
                    genlayer_votes TEXT,
                    genlayer_disagreement REAL,
                    genlayer_tx_hash TEXT,
                    decision_source TEXT,
                    execution_status TEXT,
                    execution_tx_hash TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS blacklist_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_id TEXT,
                    symbol TEXT,
                    reason TEXT,
                    source TEXT,
                    created_at TEXT,
                    UNIQUE(coin_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin_id TEXT,
                    symbol TEXT,
                    decision_source TEXT,
                    entry_price REAL,
                    size_usd REAL,
                    size_pct REAL,
                    take_profit_price REAL,
                    stop_loss_price REAL,
                    trailing_stop_pct REAL,
                    peak_price REAL,
                    execution_tx_hash TEXT,
                    status TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    exit_reason TEXT,
                    pnl_pct REAL,
                    last_price REAL
                );
                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    event_type TEXT,
                    payload_json TEXT,
                    created_at TEXT
                );
                """
            )
            for column, ddl in (
                ("summary", "TEXT"), ("risk_flags", "TEXT"), ("signal_strength", "REAL"), ("source", "TEXT"),
                ("market_context", "TEXT"), ("recent_trend", "TEXT"), ("market_stability", "REAL"),
                ("genlayer_status", "TEXT"), ("genlayer_decision", "TEXT"), ("genlayer_confidence", "REAL"),
                ("genlayer_reasoning", "TEXT"), ("genlayer_votes", "TEXT"), ("genlayer_disagreement", "REAL"),
                ("genlayer_tx_hash", "TEXT"), ("decision_source", "TEXT"), ("execution_status", "TEXT"), ("execution_tx_hash", "TEXT"),
            ):
                self._ensure_column(conn, "opportunities", column, ddl)

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def insert_market_data(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO market_data (coin_id, symbol, name, price, volume_24h, market_cap, change_24h, fetched_at)
                VALUES (:id, :symbol, :name, :current_price, :total_volume, :market_cap, :price_change_percentage_24h, :fetched_at)""",
                [{**row, "fetched_at": now} for row in rows],
            )

    def insert_dex_data(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO dex_data (symbol, pair_address, dex_id, liquidity, volume_24h, price_usd, fetched_at)
                VALUES (:symbol, :pair_address, :dex_id, :liquidity, :volume_24h, :price_usd, :fetched_at)""",
                [{**row, "fetched_at": now} for row in rows],
            )

    def insert_opportunities(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = datetime.utcnow().isoformat()
        prepared_rows = []
        for row in rows:
            prepared_rows.append({
                **row,
                "summary": row.get("summary"),
                "risk_flags": json.dumps(row.get("risk_flags") or []),
                "signal_strength": row.get("signal_strength"),
                "source": row.get("source"),
                "market_context": row.get("market_context"),
                "recent_trend": row.get("recent_trend"),
                "market_stability": row.get("market_stability"),
                "genlayer_status": row.get("genlayer_status"),
                "genlayer_decision": row.get("genlayer_decision"),
                "genlayer_confidence": row.get("genlayer_confidence"),
                "genlayer_reasoning": row.get("genlayer_reasoning"),
                "genlayer_votes": json.dumps(row.get("genlayer_votes") or []),
                "genlayer_disagreement": row.get("genlayer_disagreement"),
                "genlayer_tx_hash": row.get("genlayer_tx_hash"),
                "decision_source": row.get("decision_source"),
                "execution_status": row.get("execution_status"),
                "execution_tx_hash": row.get("execution_tx_hash"),
                "created_at": now,
            })
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO opportunities
                (coin_id, symbol, narrative, score, accumulation_score, volume_24h, liquidity, price, reason,
                 summary, risk_flags, signal_strength, source, market_context, recent_trend, market_stability,
                 genlayer_status, genlayer_decision, genlayer_confidence, genlayer_reasoning, genlayer_votes,
                 genlayer_disagreement, genlayer_tx_hash, decision_source, execution_status, execution_tx_hash, created_at)
                VALUES (:coin_id, :symbol, :narrative, :score, :accumulation_score, :volume_24h, :liquidity, :price, :reason,
                 :summary, :risk_flags, :signal_strength, :source, :market_context, :recent_trend, :market_stability,
                 :genlayer_status, :genlayer_decision, :genlayer_confidence, :genlayer_reasoning, :genlayer_votes,
                 :genlayer_disagreement, :genlayer_tx_hash, :decision_source, :execution_status, :execution_tx_hash, :created_at)""",
                prepared_rows,
            )

    def blacklist_token(self, coin_id: str | None, symbol: str, reason: str, source: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO blacklist_tokens (coin_id, symbol, reason, source, created_at) VALUES (?, ?, ?, ?, ?)", (coin_id, symbol, reason, source, now))

    def is_blacklisted(self, coin_id: str | None, symbol: str | None) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM blacklist_tokens WHERE (coin_id = ? AND coin_id IS NOT NULL) OR symbol = ? LIMIT 1",
                (coin_id, symbol or ""),
            ).fetchone()
            return row is not None

    def insert_position(self, row: dict[str, Any]) -> int:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO positions
                (coin_id, symbol, decision_source, entry_price, size_usd, size_pct, take_profit_price, stop_loss_price,
                 trailing_stop_pct, peak_price, execution_tx_hash, status, opened_at, last_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.get("coin_id"), row.get("symbol"), row.get("decision_source"), row.get("entry_price"), row.get("size_usd"), row.get("size_pct"),
                    row.get("take_profit_price"), row.get("stop_loss_price"), row.get("trailing_stop_pct"), row.get("entry_price"),
                    row.get("execution_tx_hash"), row.get("status"), now, row.get("entry_price"),
                ),
            )
            return int(cur.lastrowid)

    def get_open_positions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM positions WHERE status = 'OPEN' ORDER BY opened_at ASC")
            return [dict(row) for row in cur.fetchall()]

    def update_position_peak(self, position_id: int, current_price: float) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT peak_price FROM positions WHERE id = ?", (position_id,)).fetchone()
            peak = max(float(row["peak_price"] or 0), current_price)
            conn.execute("UPDATE positions SET peak_price = ?, last_price = ? WHERE id = ?", (peak, current_price, position_id))

    def close_position(self, position_id: int, current_price: float, exit_reason: str, pnl_pct: float) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE positions SET status = 'CLOSED', closed_at = ?, exit_reason = ?, pnl_pct = ?, last_price = ? WHERE id = ?",
                (now, exit_reason, pnl_pct, current_price, position_id),
            )

    def record_trade_event(self, symbol: str, event_type: str, payload: dict[str, Any]) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO trade_events (symbol, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (symbol, event_type, json.dumps(payload), now),
            )

    def in_global_cooldown(self, cooldown_seconds: int) -> bool:
        cutoff = (datetime.utcnow() - timedelta(seconds=cooldown_seconds)).isoformat()
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM trade_events WHERE event_type = 'ENTRY' AND created_at >= ? LIMIT 1", (cutoff,)).fetchone()
            return row is not None

    def in_token_cooldown(self, symbol: str, cooldown_seconds: int) -> bool:
        cutoff = (datetime.utcnow() - timedelta(seconds=cooldown_seconds)).isoformat()
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM trade_events WHERE symbol = ? AND event_type = 'ENTRY' AND created_at >= ? LIMIT 1", (symbol, cutoff)).fetchone()
            return row is not None

    def get_latest_opportunities(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM opportunities ORDER BY created_at DESC, score DESC LIMIT ?", (limit,))
            rows = [dict(row) for row in cur.fetchall()]
        return [self._deserialize_opportunity(row) for row in rows]

    def get_watchlist(self, low: int = 60, high: int = 70, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM opportunities WHERE score BETWEEN ? AND ? ORDER BY score DESC LIMIT ?", (low, high, limit))
            rows = [dict(row) for row in cur.fetchall()]
        return [self._deserialize_opportunity(row) for row in rows]

    def get_scan_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            coins = conn.execute("SELECT COUNT(*) AS c FROM market_data").fetchone()["c"]
            narratives = conn.execute("SELECT COUNT(*) AS c FROM opportunities WHERE narrative IS NOT NULL").fetchone()["c"]
            last = conn.execute("SELECT MAX(created_at) AS ts FROM opportunities").fetchone()["ts"]
            blacklisted = conn.execute("SELECT COUNT(*) AS c FROM blacklist_tokens").fetchone()["c"]
            open_positions = conn.execute("SELECT COUNT(*) AS c FROM positions WHERE status = 'OPEN'").fetchone()["c"]
            return {"coins_scanned": coins, "narratives_detected": narratives, "blacklisted_tokens": blacklisted, "open_positions": open_positions, "last_update": last or "N/A"}

    def top_opportunities(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM opportunities ORDER BY score DESC, created_at DESC LIMIT ?", (limit,))
            rows = [dict(row) for row in cur.fetchall()]
        return [self._deserialize_opportunity(row) for row in rows]


    def reset(self) -> None:
        db_path = Path(self.db_path)
        if db_path.exists():
            db_path.unlink()
        self._initialize()

    @staticmethod
    def _deserialize_opportunity(row: dict[str, Any]) -> dict[str, Any]:
        row["risk_flags"] = json.loads(row.get("risk_flags") or "[]")
        row["genlayer_votes"] = json.loads(row.get("genlayer_votes") or "[]")
        return row
