"""SQLite storage layer for Spider4AI market intelligence."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable

from config import settings


class Database:
    """Simple SQLite helper with table bootstrap and persistence methods."""

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
                    created_at TEXT
                );
                """
            )

    def insert_market_data(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO market_data
                (coin_id, symbol, name, price, volume_24h, market_cap, change_24h, fetched_at)
                VALUES (:id, :symbol, :name, :current_price, :total_volume, :market_cap, :price_change_percentage_24h, :fetched_at)
                """,
                [{**row, "fetched_at": now} for row in rows],
            )

    def insert_dex_data(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO dex_data
                (symbol, pair_address, dex_id, liquidity, volume_24h, price_usd, fetched_at)
                VALUES (:symbol, :pair_address, :dex_id, :liquidity, :volume_24h, :price_usd, :fetched_at)
                """,
                [{**row, "fetched_at": now} for row in rows],
            )

    def insert_opportunities(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO opportunities
                (coin_id, symbol, narrative, score, accumulation_score, volume_24h, liquidity, price, reason, created_at)
                VALUES (:coin_id, :symbol, :narrative, :score, :accumulation_score, :volume_24h, :liquidity, :price, :reason, :created_at)
                """,
                [{**row, "created_at": now} for row in rows],
            )

    def get_latest_opportunities(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM opportunities
                ORDER BY created_at DESC, score DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_watchlist(self, low: int = 60, high: int = 70, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM opportunities
                WHERE score BETWEEN ? AND ?
                ORDER BY score DESC
                LIMIT ?
                """,
                (low, high, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_scan_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            coins = conn.execute("SELECT COUNT(*) AS c FROM market_data").fetchone()["c"]
            narratives = conn.execute(
                "SELECT COUNT(*) AS c FROM opportunities WHERE narrative IS NOT NULL"
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT MAX(created_at) AS ts FROM opportunities"
            ).fetchone()["ts"]
            return {
                "coins_scanned": coins,
                "narratives_detected": narratives,
                "last_update": last or "N/A",
            }

    def top_opportunities(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM opportunities
                ORDER BY score DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
