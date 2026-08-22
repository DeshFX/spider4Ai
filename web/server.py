"""FastAPI web dashboard for Spider4AI live monitoring and operations."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agents.spider_agent import SpiderAgent
from config import settings
from execution.sepolia_executor import SepoliaExecutor
from reports.report_generator import ReportGenerator
from storage.database import Database

app = FastAPI(title="Spider4AI Web Dashboard", version="1.0.0")

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_LOG_LIMIT = 60


class _DashboardState:
    """Shared mutable state for background jobs and the action log."""

    def __init__(self) -> None:
        self.db = Database()
        self.agent: SpiderAgent | None = None
        self.scheduler: Any = None
        self.auto_scan_enabled = False
        self.current_job: str | None = None
        self.job_started_at: str | None = None
        self.actions: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def ensure_agent(self) -> SpiderAgent:
        with self._lock:
            if self.agent is None:
                self.agent = SpiderAgent()
            return self.agent

    def log(self, action: str, status: str) -> None:
        with self._lock:
            self.actions.insert(
                0,
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "action": action,
                    "status": status,
                },
            )
            del self.actions[_LOG_LIMIT:]

    def set_job(self, job: str | None) -> bool:
        with self._lock:
            if job is not None and self.current_job is not None:
                return False
            self.current_job = job
            self.job_started_at = (
                datetime.now().isoformat(timespec="seconds") if job else None
            )
            return True

    def run_job(self, name: str, label: str, func: Callable[[], Any]) -> bool:
        if not self.set_job(name):
            return False
        self.log(label, "running")

        def _worker() -> None:
            try:
                result = func()
                self.log(label, f"done: {_summarize(name, result)}")
            except Exception as exc:
                self.log(label, f"failed: {exc}")
            finally:
                self.set_job(None)

        threading.Thread(target=_worker, daemon=True).start()
        return True


def _summarize(job: str, result: Any) -> str:
    if job == "scan":
        return f"{len(result or [])} opportunities"
    return str(result)


state = _DashboardState()

_ALPHA_TTL_SECONDS = 600
_alpha_cache: dict[str, Any] = {"data": None, "at": 0.0}


def _on_agent_event(event: str, message: str) -> None:
    state.log(f"[{event}]", message)


@app.on_event("startup")
def _warmup() -> None:
    agent = state.ensure_agent()
    agent.event_sink = _on_agent_event
    state.log("Server start", "Web dashboard aktif, agent siap")


class DryRunRequest(BaseModel):
    enabled: bool


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    with state.db._connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok"}


_OPPORTUNITY_FIELDS = (
    "symbol",
    "score",
    "narrative",
    "price",
    "volume_24h",
    "liquidity",
    "market_cap",
    "genlayer_decision",
    "genlayer_confidence",
    "decision_source",
    "created_at",
)


def _slim(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{k: row.get(k) for k in fields if k in row} for row in rows]


@app.get("/api/opportunities")
def opportunities() -> list[dict[str, Any]]:
    return _slim(state.db.get_latest_opportunities(10), _OPPORTUNITY_FIELDS)


@app.get("/api/watchlist")
def watchlist() -> dict[str, Any]:
    return {
        "tokens": state.db.get_watchlist(),
        "meme": _slim(
            state.db.get_meme_opportunities(),
            (
                "symbol",
                "narrative",
                "score",
                "price",
                "volume_24h",
                "liquidity",
                "genlayer_decision",
                "genlayer_confidence",
                "decision_source",
                "execution_status",
                "created_at",
            ),
        ),
        "alpha_hunter_enabled": settings.alpha_hunter_enabled,
    }


@app.get("/api/logs")
def logs() -> list[dict[str, str]]:
    return state.actions


@app.get("/api/status")
def status() -> dict[str, Any]:
    scan_status = state.db.get_scan_status()
    usage = state.db.get_api_usage()
    return {
        "coins_scanned": scan_status.get("coins_scanned", 0),
        "narratives_detected": scan_status.get("narratives_detected", 0),
        "blacklisted_tokens": scan_status.get("blacklisted_tokens", 0),
        "open_positions": scan_status.get("open_positions", 0),
        "last_update": scan_status.get("last_update"),
        "dry_run": settings.dry_run,
        "genlayer_contract": settings.genlayer_contract_address,
        "auto_scan": state.auto_scan_enabled,
        "current_job": state.current_job,
        "job_started_at": state.job_started_at,
        "cambrian_today": usage.get("today_calls", 0),
        "cambrian_month": usage.get("month_calls", 0),
        "cambrian_budget": settings.cambrian_monthly_budget,
        "local_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/api/scan")
def scan_now() -> dict[str, str]:
    agent = state.ensure_agent()
    started = state.run_job("scan", "Market scan", agent.run_cycle)
    if not started:
        raise HTTPException(409, f"Job '{state.current_job}' sedang berjalan")
    return {"message": "Scan dimulai di background (1-3 menit, pantau Action Log)"}


@app.post("/api/scan-meme")
def scan_meme() -> dict[str, str]:
    agent = state.ensure_agent()
    started = state.run_job(
        "scan", "Meme scan", lambda: agent.run_cycle(force_alpha=True)
    )
    if not started:
        raise HTTPException(409, f"Job '{state.current_job}' sedang berjalan")
    return {
        "message": "Meme scan dimulai: momentum + alpha tweets, FDV 10k-200k (1-3 menit)"
    }


@app.post("/api/auto-scan")
def toggle_auto_scan() -> dict[str, Any]:
    if state.auto_scan_enabled and state.scheduler:
        state.scheduler.shutdown(wait=False)
        state.scheduler = None
        state.auto_scan_enabled = False
        message = "Auto scan dimatikan"
    else:
        agent = state.ensure_agent()
        state.scheduler = agent.start_scheduler()
        state.auto_scan_enabled = True
        message = f"Auto scan aktif (tiap {settings.scheduler_minutes} menit)"
    state.log("Auto scan toggle", message)
    return {"auto_scan": state.auto_scan_enabled, "message": message}


@app.post("/api/report")
def generate_report() -> dict[str, str]:
    generator = ReportGenerator()
    started = state.run_job("report", "Generate report", generator.generate_daily_report)
    if not started:
        raise HTTPException(409, f"Job '{state.current_job}' sedang berjalan")
    return {"message": "Report digenerate di background"}


@app.post("/api/test-trade")
def test_trade() -> dict[str, str]:
    executor = SepoliaExecutor()
    started = state.run_job(
        "test_trade", "Test trade", executor.simulate_test_transaction
    )
    if not started:
        raise HTTPException(409, f"Job '{state.current_job}' sedang berjalan")
    return {"message": "Test trade dikirim di background"}


@app.post("/api/dry-run")
def set_dry_run(payload: DryRunRequest) -> dict[str, Any]:
    previous = settings.dry_run
    object.__setattr__(settings, "dry_run", payload.enabled)
    mode = "DRY_RUN ON" if payload.enabled else "LIVE TRADING"
    state.log("Dry run toggle", f"{previous} -> {settings.dry_run} ({mode})")
    return {"dry_run": settings.dry_run}


def _fetch_alpha() -> dict[str, Any]:
    agent = state.ensure_agent()
    momentum = agent.cambrian.get_social_momentum(limit=10)
    tweets = agent.cambrian.get_alpha_tweets(limit=10)
    return {
        "momentum": momentum,
        "tweets": tweets,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _alpha_fresh() -> bool:
    return (
        _alpha_cache["data"] is not None
        and time.time() - _alpha_cache["at"] < _ALPHA_TTL_SECONDS
    )


@app.get("/api/alpha")
def alpha() -> dict[str, Any]:
    fresh = _alpha_fresh()
    if not fresh:
        try:
            data = _fetch_alpha()
            _alpha_cache.update(data=data, at=time.time())
            total = len(data["momentum"]) + len(data["tweets"])
            if total:
                state.log("Alpha radar", f"Data sosial diperbarui (cache {_ALPHA_TTL_SECONDS // 60} menit)")
            else:
                state.log("Alpha radar", "Sumber sosial kosong - kemungkinan rate limit API atau pasar sepi")
        except Exception as exc:
            state.log("Alpha radar", f"failed: {exc}")
            raise HTTPException(503, f"Alpha fetch gagal: {exc}")
    return {**_alpha_cache["data"], "cached": fresh}


@app.post("/api/alpha/refresh")
def alpha_refresh() -> dict[str, Any]:
    try:
        data = _fetch_alpha()
    except Exception as exc:
        state.log("Alpha radar", f"failed: {exc}")
        raise HTTPException(503, f"Alpha fetch gagal: {exc}")
    _alpha_cache.update(data=data, at=time.time())
    state.log(
        "Alpha radar",
        f"Refresh manual: {len(data['momentum'])} momentum, {len(data['tweets'])} tweet",
    )
    return {**data, "cached": False}
