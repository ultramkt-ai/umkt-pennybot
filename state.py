"""
state.py — Persistência com SQLite + JSON snapshots.

Tabelas:
  - positions: posições abertas e fechadas
  - trades_history: log de todas as operações
  - market_cache: cache de dados de mercado da Gamma API

JSON snapshots para backup e integração externa.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DB_PATH, SNAPSHOTS_DIR


# ─── Schema ──────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    shares          REAL NOT NULL,
    cost            REAL NOT NULL,
    current_price   REAL,
    token_id        TEXT DEFAULT '',
    target_exit     REAL,
    stop_price      REAL,
    bounce_exit_pct REAL,
    category        TEXT DEFAULT 'other',
    status          TEXT DEFAULT 'open',
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    market_question TEXT,
    UNIQUE(market_id, strategy, side, opened_at)
);

CREATE TABLE IF NOT EXISTS trades_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL,
    action          TEXT NOT NULL,
    price           REAL NOT NULL,
    shares          REAL NOT NULL,
    timestamp       TEXT NOT NULL,
    reason          TEXT,
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS market_cache (
    market_id       TEXT PRIMARY KEY,
    condition_id    TEXT,
    event_id        TEXT,
    question        TEXT,
    category        TEXT,
    end_date        TEXT,
    liquidity       REAL,
    yes_price       REAL,
    no_price        REAL,
    volume          REAL,
    active          INTEGER DEFAULT 1,
    resolved        INTEGER DEFAULT 0,
    resolution      TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy);
CREATE INDEX IF NOT EXISTS idx_positions_event ON positions(event_id);
CREATE INDEX IF NOT EXISTS idx_positions_category ON positions(category);
CREATE INDEX IF NOT EXISTS idx_market_cache_active ON market_cache(active);
"""


# ─── Database Manager ────────────────────────────────────────────────────────

class StateManager:
    """Gerencia SQLite + JSON snapshots. Thread-safe via contextmanager."""

    def __init__(self, db_path: str = DB_PATH, snapshots_dir: str = SNAPSHOTS_DIR):
        self.db_path = db_path
        self.snapshots_dir = snapshots_dir

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(snapshots_dir).mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Positions ───────────────────────────────────────────────────────

    def open_position(
        self,
        market_id: str,
        condition_id: str,
        event_id: str,
        strategy: str,
        side: str,
        entry_price: float,
        shares: float,
        token_id: str = "",
        target_exit: float | None = None,
        stop_price: float | None = None,
        bounce_exit_pct: float | None = None,
        category: str = "other",
        market_question: str = "",
    ) -> int:
        """Abre posição e registra trade. Retorna position_id."""
        now = datetime.now(timezone.utc).isoformat()
        cost = entry_price * shares

        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO positions
                   (market_id, condition_id, event_id, strategy, side,
                    entry_price, shares, cost, current_price, token_id,
                    target_exit, stop_price, bounce_exit_pct,
                    category, status, opened_at, market_question)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                (market_id, condition_id, event_id, strategy, side,
                 entry_price, shares, cost, entry_price, token_id,
                 target_exit, stop_price, bounce_exit_pct,
                 category, now, market_question),
            )
            position_id = cursor.lastrowid

            conn.execute(
                """INSERT INTO trades_history
                   (position_id, action, price, shares, timestamp, reason)
                   VALUES (?, 'open', ?, ?, ?, 'entry')""",
                (position_id, entry_price, shares, now),
            )

        return position_id

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        reason: str,
    ) -> dict[str, Any]:
        """Fecha posição, calcula PnL, registra trade. Retorna dados da posição."""
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ? AND status = 'open'",
                (position_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Posição {position_id} não encontrada ou já fechada")

            pnl = (exit_price - row["entry_price"]) * row["shares"]

            conn.execute(
                """UPDATE positions
                   SET status = 'closed', exit_price = ?, exit_reason = ?,
                       pnl = ?, closed_at = ?, current_price = ?
                   WHERE id = ?""",
                (exit_price, reason, pnl, now, exit_price, position_id),
            )

            conn.execute(
                """INSERT INTO trades_history
                   (position_id, action, price, shares, timestamp, reason)
                   VALUES (?, 'close', ?, ?, ?, ?)""",
                (position_id, exit_price, row["shares"], now, reason),
            )

        return {
            "position_id": position_id,
            "market_id": row["market_id"],
            "strategy": row["strategy"],
            "side": row["side"],
            "entry_price": row["entry_price"],
            "exit_price": exit_price,
            "shares": row["shares"],
            "pnl": pnl,
            "reason": reason,
            "market_question": row["market_question"],
        }

    def update_current_price(self, position_id: int, price: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE positions SET current_price = ? WHERE id = ?",
                (price, position_id),
            )

    def get_open_positions(self, strategy: str | None = None) -> list[dict]:
        query = "SELECT * FROM positions WHERE status = 'open'"
        params: list = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        query += " ORDER BY opened_at DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_positions_for_event(self, event_id: str, strategy: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM positions
                   WHERE event_id = ? AND strategy = ? AND status = 'open'""",
                (event_id, strategy),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_open_positions(self, strategy: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM positions WHERE strategy = ? AND status = 'open'",
                (strategy,),
            ).fetchone()
        return row["cnt"]

    def get_all_positions(
        self,
        strategy: str | None = None,
        status: str | None = None,
        category: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        query = "SELECT * FROM positions WHERE 1=1"
        params: list = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY opened_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ─── Market Cache ────────────────────────────────────────────────────

    def upsert_market(self, market: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO market_cache
                   (market_id, condition_id, event_id, question, category,
                    end_date, liquidity, yes_price, no_price, volume,
                    active, resolved, resolution, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market_id) DO UPDATE SET
                    condition_id=excluded.condition_id,
                    event_id=excluded.event_id,
                    question=excluded.question,
                    category=excluded.category,
                    end_date=excluded.end_date,
                    liquidity=excluded.liquidity,
                    yes_price=excluded.yes_price,
                    no_price=excluded.no_price,
                    volume=excluded.volume,
                    active=excluded.active,
                    resolved=excluded.resolved,
                    resolution=excluded.resolution,
                    updated_at=excluded.updated_at""",
                (
                    market["market_id"],
                    market.get("condition_id", ""),
                    market.get("event_id", ""),
                    market.get("question", ""),
                    market.get("category", "other"),
                    market.get("end_date", ""),
                    market.get("liquidity", 0),
                    market.get("yes_price", 0),
                    market.get("no_price", 0),
                    market.get("volume", 0),
                    market.get("active", 1),
                    market.get("resolved", 0),
                    market.get("resolution"),
                    now,
                ),
            )

    def get_cached_market(self, market_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM market_cache WHERE market_id = ?",
                (market_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_active_markets(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM market_cache WHERE active = 1 AND resolved = 0",
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── JSON Snapshots ──────────────────────────────────────────────────

    def save_snapshot(self) -> str:
        """Salva estado completo em JSON. Retorna path do arquivo."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{timestamp}.json"
        filepath = os.path.join(self.snapshots_dir, filename)

        with self._connect() as conn:
            positions = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
            trades = [dict(r) for r in conn.execute("SELECT * FROM trades_history").fetchall()]
            markets = [dict(r) for r in conn.execute("SELECT * FROM market_cache").fetchall()]

        snapshot = {
            "timestamp": timestamp,
            "positions": positions,
            "trades_history": trades,
            "market_cache": markets,
        }

        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)

        return filepath

    # ─── Stats rápidas ───────────────────────────────────────────────────

    def get_stats_summary(self) -> dict:
        with self._connect() as conn:
            open_count = conn.execute(
                "SELECT COUNT(*) as c FROM positions WHERE status = 'open'"
            ).fetchone()["c"]

            closed = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                          SUM(pnl) as total_pnl,
                          SUM(cost) as total_invested
                   FROM positions WHERE status IN ('closed', 'resolved')"""
            ).fetchone()

        total_closed = closed["total"] or 0
        wins = closed["wins"] or 0
        total_pnl = closed["total_pnl"] or 0.0
        total_invested = closed["total_invested"] or 0.0

        return {
            "open_positions": open_count,
            "closed_positions": total_closed,
            "win_rate": wins / total_closed if total_closed > 0 else 0.0,
            "total_pnl": round(total_pnl, 4),
            "total_invested": round(total_invested, 4),
            "roi": round(total_pnl / total_invested, 4) if total_invested > 0 else 0.0,
        }
