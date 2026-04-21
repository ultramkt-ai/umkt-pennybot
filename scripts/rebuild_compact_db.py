from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

SRC_DB = Path('/home/rafael/umkt-pennybot/data/umkt_pennybot.db')
DATA_DIR = SRC_DB.parent
TMP_DB = DATA_DIR / 'umkt_pennybot_compact_rebuild.db'
BACKUP_DB = DATA_DIR / f"umkt_pennybot_before_rebuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite-backup"

SCHEMA_SQL = '''
PRAGMA journal_mode=DELETE;
PRAGMA foreign_keys=OFF;

CREATE TABLE positions (
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
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_strategy ON positions(strategy);
CREATE INDEX idx_positions_event ON positions(event_id);
CREATE INDEX idx_positions_category ON positions(category);

CREATE TABLE trades_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL,
    action          TEXT NOT NULL,
    price           REAL NOT NULL,
    shares          REAL NOT NULL,
    timestamp       TEXT NOT NULL,
    reason          TEXT,
    FOREIGN KEY (position_id) REFERENCES positions(id)
);
CREATE INDEX idx_trades_history_position ON trades_history(position_id);
CREATE INDEX idx_trades_history_timestamp ON trades_history(timestamp);

CREATE TABLE strategy_runtime (
    strategy        TEXT PRIMARY KEY,
    bankroll        REAL,
    updated_at      TEXT NOT NULL,
    payload_json    TEXT
);

CREATE TABLE market_cache (
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
    updated_at      TEXT NOT NULL,
    yes_token_id    TEXT,
    no_token_id     TEXT
);
CREATE INDEX idx_market_cache_active ON market_cache(active);
'''

COPY_TABLES = {
    'positions': [
        'id','market_id','condition_id','event_id','strategy','side','entry_price','shares','cost',
        'current_price','token_id','target_exit','stop_price','bounce_exit_pct','category','status',
        'exit_price','exit_reason','pnl','opened_at','closed_at','market_question'
    ],
    'trades_history': [
        'id','position_id','action','price','shares','timestamp','reason'
    ],
    'strategy_runtime': [
        'strategy','bankroll','updated_at','payload_json'
    ],
    'market_cache': [
        'market_id','condition_id','event_id','question','category','end_date','liquidity','yes_price',
        'no_price','volume','active','resolved','resolution','updated_at','yes_token_id','no_token_id'
    ],
}


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, columns: list[str]) -> int:
    col_csv = ', '.join(columns)
    rows = src.execute(f'SELECT {col_csv} FROM {table}').fetchall()
    placeholders = ', '.join('?' for _ in columns)
    dst.executemany(
        f'INSERT INTO {table} ({col_csv}) VALUES ({placeholders})',
        rows,
    )
    return len(rows)


def main() -> None:
    if not SRC_DB.exists():
        raise SystemExit(f'Banco não encontrado: {SRC_DB}')

    TMP_DB.unlink(missing_ok=True)

    src = sqlite3.connect(SRC_DB)
    dst = sqlite3.connect(TMP_DB)
    try:
        dst.executescript(SCHEMA_SQL)

        copied = {}
        for table, columns in COPY_TABLES.items():
            copied[table] = copy_table(src, dst, table, columns)

        dst.commit()
        dst.close()
        src.close()

        os.replace(SRC_DB, BACKUP_DB)
        os.replace(TMP_DB, SRC_DB)

        print({'backup_db': str(BACKUP_DB), 'copied': copied, 'new_db': str(SRC_DB)})
    finally:
        try:
            src.close()
        except Exception:
            pass
        try:
            dst.close()
        except Exception:
            pass
        TMP_DB.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
