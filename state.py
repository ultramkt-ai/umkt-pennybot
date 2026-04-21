"""
state.py — Persistência com SQLite + JSON snapshots.

Tabelas:
  - positions: posições abertas e fechadas
  - trades_history: log simplificado de open/close
  - ledger_events: trilha imutável e auditável de eventos do bot
  - market_cache: cache de dados de mercado da Gamma API

JSON snapshots para backup e integração externa.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

CREATE TABLE IF NOT EXISTS ledger_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER,
    event_type      TEXT NOT NULL,
    strategy        TEXT,
    market_id       TEXT,
    event_id        TEXT,
    condition_id    TEXT,
    side            TEXT,
    position_status TEXT,
    price           REAL,
    shares          REAL,
    notional        REAL,
    pnl             REAL,
    reason          TEXT,
    source          TEXT,
    payload_json    TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS strategy_runtime (
    strategy        TEXT PRIMARY KEY,
    bankroll        REAL,
    updated_at      TEXT NOT NULL,
    payload_json    TEXT
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
    yes_token_id    TEXT,
    no_token_id     TEXT,
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
CREATE INDEX IF NOT EXISTS idx_trades_history_position ON trades_history(position_id);
CREATE INDEX IF NOT EXISTS idx_trades_history_timestamp ON trades_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_ledger_events_position ON ledger_events(position_id);
CREATE INDEX IF NOT EXISTS idx_ledger_events_type ON ledger_events(event_type);
CREATE INDEX IF NOT EXISTS idx_ledger_events_strategy ON ledger_events(strategy);
CREATE INDEX IF NOT EXISTS idx_ledger_events_created_at ON ledger_events(created_at);
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
            self._ensure_column(conn, "market_cache", "yes_token_id", "TEXT")
            self._ensure_column(conn, "market_cache", "no_token_id", "TEXT")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        column_sql: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")

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

    # ─── Ledger helpers ─────────────────────────────────────────────────

    def _json_dumps(self, payload: dict[str, Any] | None) -> str | None:
        if payload is None:
            return None
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _record_ledger_event(
        self,
        conn: sqlite3.Connection,
        *,
        position_id: int | None = None,
        event_type: str,
        strategy: str | None = None,
        market_id: str | None = None,
        event_id: str | None = None,
        condition_id: str | None = None,
        side: str | None = None,
        position_status: str | None = None,
        price: float | None = None,
        shares: float | None = None,
        notional: float | None = None,
        pnl: float | None = None,
        reason: str | None = None,
        source: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        ts = created_at or datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO ledger_events
               (position_id, event_type, strategy, market_id, event_id, condition_id,
                side, position_status, price, shares, notional, pnl, reason, source,
                payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                position_id,
                event_type,
                strategy,
                market_id,
                event_id,
                condition_id,
                side,
                position_status,
                price,
                shares,
                notional,
                pnl,
                reason,
                source,
                self._json_dumps(payload),
                ts,
            ),
        )

    def record_ledger_event(self, **kwargs) -> None:
        with self._connect() as conn:
            self._record_ledger_event(conn, **kwargs)

    def record_ledger_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        with self._connect() as conn:
            for event in events:
                self._record_ledger_event(conn, **event)

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
        audit_payload: dict[str, Any] | None = None,
        source: str = "paper_engine",
    ) -> int:
        """Abre posição e registra trade simplificado + ledger auditável."""
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
                (
                    market_id,
                    condition_id,
                    event_id,
                    strategy,
                    side,
                    entry_price,
                    shares,
                    cost,
                    entry_price,
                    token_id,
                    target_exit,
                    stop_price,
                    bounce_exit_pct,
                    category,
                    now,
                    market_question,
                ),
            )
            position_id = cursor.lastrowid

            conn.execute(
                """INSERT INTO trades_history
                   (position_id, action, price, shares, timestamp, reason)
                   VALUES (?, 'open', ?, ?, ?, 'entry')""",
                (position_id, entry_price, shares, now),
            )

            self._record_ledger_event(
                conn,
                position_id=position_id,
                event_type="position_open",
                strategy=strategy,
                market_id=market_id,
                event_id=event_id,
                condition_id=condition_id,
                side=side,
                position_status="open",
                price=entry_price,
                shares=shares,
                notional=cost,
                reason="entry",
                source=source,
                payload={
                    "market_question": market_question,
                    "category": category,
                    "token_id": token_id,
                    "target_exit": target_exit,
                    "stop_price": stop_price,
                    "bounce_exit_pct": bounce_exit_pct,
                    **(audit_payload or {}),
                },
                created_at=now,
            )

        return position_id

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        reason: str,
        audit_payload: dict[str, Any] | None = None,
        source: str = "paper_engine",
    ) -> dict[str, Any]:
        """Fecha posição, calcula PnL, registra trade simplificado + ledger."""
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ? AND status = 'open'",
                (position_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Posição {position_id} não encontrada ou já fechada")

            pnl = (exit_price - row["entry_price"]) * row["shares"]
            close_status = "resolved" if reason.startswith("resolved_") else "closed"

            conn.execute(
                """UPDATE positions
                   SET status = ?, exit_price = ?, exit_reason = ?,
                       pnl = ?, closed_at = ?, current_price = ?
                   WHERE id = ?""",
                (close_status, exit_price, reason, pnl, now, exit_price, position_id),
            )

            conn.execute(
                """INSERT INTO trades_history
                   (position_id, action, price, shares, timestamp, reason)
                   VALUES (?, 'close', ?, ?, ?, ?)""",
                (position_id, exit_price, row["shares"], now, reason),
            )

            self._record_ledger_event(
                conn,
                position_id=position_id,
                event_type="position_close",
                strategy=row["strategy"],
                market_id=row["market_id"],
                event_id=row["event_id"],
                condition_id=row["condition_id"],
                side=row["side"],
                position_status=close_status,
                price=exit_price,
                shares=row["shares"],
                notional=exit_price * row["shares"],
                pnl=pnl,
                reason=reason,
                source=source,
                payload={
                    "market_question": row["market_question"],
                    "entry_price": row["entry_price"],
                    "target_exit": row["target_exit"],
                    "stop_price": row["stop_price"],
                    "bounce_exit_pct": row["bounce_exit_pct"],
                    "current_price_before_close": row["current_price"],
                    **(audit_payload or {}),
                },
                created_at=now,
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
            "status": close_status,
        }

    def update_current_price(
        self,
        position_id: int,
        price: float,
        *,
        source: str = "monitor",
        payload: dict[str, Any] | None = None,
        record_ledger: bool = True,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Posição {position_id} não encontrada")

            old_price = row["current_price"]
            conn.execute(
                "UPDATE positions SET current_price = ? WHERE id = ?",
                (price, position_id),
            )

            if record_ledger and old_price != price:
                self._record_ledger_event(
                    conn,
                    position_id=position_id,
                    event_type="mark_to_market",
                    strategy=row["strategy"],
                    market_id=row["market_id"],
                    event_id=row["event_id"],
                    condition_id=row["condition_id"],
                    side=row["side"],
                    position_status=row["status"],
                    price=price,
                    shares=row["shares"],
                    notional=price * row["shares"],
                    reason="price_update",
                    source=source,
                    payload={
                        "old_price": old_price,
                        "new_price": price,
                        **(payload or {}),
                    },
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

    def get_ledger_events(
        self,
        *,
        strategy: str | None = None,
        event_type: str | None = None,
        position_id: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        query = "SELECT * FROM ledger_events WHERE 1=1"
        params: list[Any] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if position_id is not None:
            query += " AND position_id = ?"
            params.append(position_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def set_strategy_runtime(self, strategy: str, bankroll: float, payload: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO strategy_runtime (strategy, bankroll, updated_at, payload_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(strategy) DO UPDATE SET
                     bankroll=excluded.bankroll,
                     updated_at=excluded.updated_at,
                     payload_json=excluded.payload_json""",
                (strategy, bankroll, now, self._json_dumps(payload)),
            )

    def get_strategy_runtime(self, strategy: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_runtime WHERE strategy = ?",
                (strategy,),
            ).fetchone()
        return dict(row) if row else None

    def get_open_invested(self, strategy: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0) AS total FROM positions WHERE strategy = ? AND status = 'open'",
                (strategy,),
            ).fetchone()
        return float(row['total'] or 0.0)

    # ─── Market Cache ────────────────────────────────────────────────────

    def _market_upsert_params(self, market: dict, now: str | None = None) -> tuple:
        ts = now or datetime.now(timezone.utc).isoformat()
        return (
            market["market_id"],
            market.get("condition_id", ""),
            market.get("event_id", ""),
            market.get("question", ""),
            market.get("category", "other"),
            market.get("end_date", ""),
            market.get("liquidity", 0),
            market.get("yes_price", 0),
            market.get("no_price", 0),
            market.get("yes_token_id", ""),
            market.get("no_token_id", ""),
            market.get("volume", 0),
            market.get("active", 1),
            market.get("resolved", 0),
            market.get("resolution"),
            ts,
        )

    def upsert_market(self, market: dict) -> None:
        self.upsert_markets([market])

    def upsert_markets(self, markets: list[dict]) -> None:
        if not markets:
            return

        now = datetime.now(timezone.utc).isoformat()
        rows = [self._market_upsert_params(market, now) for market in markets]

        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO market_cache
                   (market_id, condition_id, event_id, question, category,
                    end_date, liquidity, yes_price, no_price, yes_token_id, no_token_id, volume,
                    active, resolved, resolution, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market_id) DO UPDATE SET
                    condition_id=excluded.condition_id,
                    event_id=excluded.event_id,
                    question=excluded.question,
                    category=excluded.category,
                    end_date=excluded.end_date,
                    liquidity=excluded.liquidity,
                    yes_price=excluded.yes_price,
                    no_price=excluded.no_price,
                    yes_token_id=excluded.yes_token_id,
                    no_token_id=excluded.no_token_id,
                    volume=excluded.volume,
                    active=excluded.active,
                    resolved=excluded.resolved,
                    resolution=excluded.resolution,
                    updated_at=excluded.updated_at""",
                rows,
            )

    def update_position_token_id(
        self,
        position_id: int,
        token_id: str,
        *,
        source: str = "token_backfill",
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not token_id:
            raise ValueError("token_id vazio não pode ser persistido")

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Posição {position_id} não encontrada")

            old_token_id = row["token_id"] or ""
            conn.execute(
                "UPDATE positions SET token_id = ? WHERE id = ?",
                (token_id, position_id),
            )

            self._record_ledger_event(
                conn,
                position_id=position_id,
                event_type="position_token_backfilled",
                strategy=row["strategy"],
                market_id=row["market_id"],
                event_id=row["event_id"],
                condition_id=row["condition_id"],
                side=row["side"],
                position_status=row["status"],
                reason="token_id_restored",
                source=source,
                payload={
                    "old_token_id": old_token_id,
                    "new_token_id": token_id,
                    **(payload or {}),
                },
            )

    def update_position_risk_params(
        self,
        position_id: int,
        *,
        target_exit: float | None = None,
        stop_price: float | None = None,
        bounce_exit_pct: float | None = None,
        source: str = "risk_repair",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Posição {position_id} não encontrada")

            old_target_exit = row["target_exit"]
            old_stop_price = row["stop_price"]
            old_bounce_exit_pct = row["bounce_exit_pct"]

            new_target_exit = old_target_exit if target_exit is None else target_exit
            new_stop_price = old_stop_price if stop_price is None else stop_price
            new_bounce_exit_pct = old_bounce_exit_pct if bounce_exit_pct is None else bounce_exit_pct

            conn.execute(
                """UPDATE positions
                   SET target_exit = ?, stop_price = ?, bounce_exit_pct = ?
                   WHERE id = ?""",
                (new_target_exit, new_stop_price, new_bounce_exit_pct, position_id),
            )

            self._record_ledger_event(
                conn,
                position_id=position_id,
                event_type="position_risk_params_updated",
                strategy=row["strategy"],
                market_id=row["market_id"],
                event_id=row["event_id"],
                condition_id=row["condition_id"],
                side=row["side"],
                position_status=row["status"],
                reason="risk_params_recomputed",
                source=source,
                payload={
                    "old_target_exit": old_target_exit,
                    "new_target_exit": new_target_exit,
                    "old_stop_price": old_stop_price,
                    "new_stop_price": new_stop_price,
                    "old_bounce_exit_pct": old_bounce_exit_pct,
                    "new_bounce_exit_pct": new_bounce_exit_pct,
                    **(payload or {}),
                },
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
        """Salva snapshot operacional enxuto em JSON. Retorna path do arquivo."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{timestamp}.json"
        filepath = os.path.join(self.snapshots_dir, filename)

        with self._connect() as conn:
            positions = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
            trades = [dict(r) for r in conn.execute("SELECT * FROM trades_history").fetchall()]
            markets = [dict(r) for r in conn.execute("SELECT * FROM market_cache").fetchall()]
            runtime = [dict(r) for r in conn.execute("SELECT * FROM strategy_runtime").fetchall()]
            recent_ledger = [
                dict(r)
                for r in conn.execute(
                    """SELECT * FROM ledger_events
                       WHERE event_type IN (
                           'position_open',
                           'position_close',
                           'mark_to_market',
                           'position_token_backfilled',
                           'position_risk_params_updated',
                           'token_repair_cycle',
                           'risk_param_repair_cycle',
                           'scan_cycle',
                           'strategy_scan_summary'
                       )
                       ORDER BY created_at DESC, id DESC
                       LIMIT 2000"""
                ).fetchall()
            ]

        snapshot = {
            "timestamp": timestamp,
            "snapshot_kind": "operational_compact",
            "positions": positions,
            "trades_history": trades,
            "strategy_runtime": runtime,
            "market_cache": markets,
            "recent_ledger_events": recent_ledger,
        }

        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)

        return filepath
    
    def cleanup_ledger_events(
        self,
        *,
        drop_event_types: tuple[str, ...] = (
            'market_rejected',
            'signal_generated',
            'signal_not_selected',
            'market_skipped_capacity',
        ),
        keep_recent_mark_to_market_days: int = 7,
        keep_recent_bounce_alert_days: int = 14,
    ) -> dict[str, int]:
        """Remove eventos volumosos e antigos do ledger para conter crescimento."""
        deleted_by_type: dict[str, int] = {}
        now = datetime.now(timezone.utc)
        mark_cutoff = (now - timedelta(days=keep_recent_mark_to_market_days)).isoformat()
        bounce_cutoff = (now - timedelta(days=keep_recent_bounce_alert_days)).isoformat()

        with self._connect() as conn:
            for event_type in drop_event_types:
                cursor = conn.execute(
                    "DELETE FROM ledger_events WHERE event_type = ?",
                    (event_type,),
                )
                deleted_by_type[event_type] = cursor.rowcount or 0

            mark_deleted = conn.execute(
                "DELETE FROM ledger_events WHERE event_type = 'mark_to_market' AND created_at < ?",
                (mark_cutoff,),
            ).rowcount or 0
            deleted_by_type['mark_to_market_old'] = mark_deleted

            bounce_deleted = conn.execute(
                "DELETE FROM ledger_events WHERE event_type = 'bounce_alert' AND created_at < ?",
                (bounce_cutoff,),
            ).rowcount or 0
            deleted_by_type['bounce_alert_old'] = bounce_deleted

        deleted_by_type['total_deleted'] = sum(deleted_by_type.values())
        return deleted_by_type

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
