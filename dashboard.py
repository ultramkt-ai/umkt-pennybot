"""
dashboard.py — Servidor web para o dashboard do Penny-Bot.

Backend Flask que lê o SQLite do bot e serve um dashboard em tempo real.
Atualização automática via polling (5s).

Uso:
    python3 dashboard.py
    → http://localhost:5000
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string

from config import DB_PATH, STRATEGIES
from state import StateManager


# ─── Config ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

state = StateManager(db_path=str(Path(DB_PATH)))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_portfolio_summary() -> dict:
    stats = state.get_stats_summary()
    open_positions = state.get_open_positions()

    positions_value = sum(
        (p.get("current_price") or p["entry_price"]) * p["shares"]
        for p in open_positions
    )

    initial_bankroll = 10_000.0
    invested = sum(p["cost"] for p in open_positions)
    realized_pnl = stats.get("total_pnl", 0.0)
    cash = initial_bankroll - invested + realized_pnl
    portfolio_value = cash + positions_value
    session_pnl = realized_pnl + sum(
        ((p.get("current_price") or p["entry_price"]) - p["entry_price"]) * p["shares"]
        for p in open_positions
    )

    monitored = len(state.get_active_markets())

    return {
        "monitored": monitored,
        "open_positions": len(open_positions),
        "cash": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "portfolio_value": round(portfolio_value, 2),
        "session_pnl": round(session_pnl, 2),
        "total_invested": stats.get("total_invested", 0.0),
        "total_pnl": stats.get("total_pnl", 0.0),
        "win_rate": stats.get("win_rate", 0.0),
        "closed_positions": stats.get("closed_positions", 0),
        "last_update": datetime.now(timezone.utc).isoformat(),
    }


def get_open_positions() -> list[dict]:
    positions = state.get_open_positions()
    enriched = []

    for p in positions:
        current_price = p.get("current_price") or p["entry_price"]
        market_value = current_price * p["shares"]
        pnl = (current_price - p["entry_price"]) * p["shares"]
        pnl_pct = (pnl / p["cost"]) * 100 if p["cost"] > 0 else 0
        potential_win = (1.0 - p["entry_price"]) * p["shares"]
        potential_win_pct = ((1.0 - p["entry_price"]) / p["entry_price"]) * 100 if p["entry_price"] > 0 else 0

        enriched.append({
            "id": p["id"],
            "market_id": p["market_id"],
            "question": p.get("market_question", ""),
            "slug": p["market_id"].split("/")[-1] if "/" in p["market_id"] else p["market_id"][:40],
            "side": p["side"],
            "shares": round(p["shares"], 4),
            "entry_price": p["entry_price"],
            "current_price": current_price,
            "market_value": round(market_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "potential_win": round(potential_win, 2),
            "potential_win_pct": round(potential_win_pct, 2),
            "opened_at": p["opened_at"],
            "strategy": p["strategy"],
            "category": p.get("category", "other"),
        })

    enriched.sort(key=lambda x: x["pnl_pct"], reverse=True)
    return enriched


def get_recent_trades(limit: int = 20) -> list[dict]:
    with state._connect() as conn:
        rows = conn.execute(
            """SELECT th.*, p.market_id, p.strategy, p.side, p.market_question
               FROM trades_history th
               JOIN positions p ON th.position_id = p.id
               ORDER BY th.timestamp DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    trades = []
    for r in rows:
        trades.append({
            "id": r["id"],
            "position_id": r["position_id"],
            "action": r["action"],
            "price": r["price"],
            "shares": r["shares"],
            "timestamp": r["timestamp"],
            "reason": r.get("reason", ""),
            "market_id": r["market_id"],
            "slug": r["market_id"].split("/")[-1] if "/" in r["market_id"] else r["market_id"][:40],
            "question": (r.get("market_question") or "")[:60],
            "side": r["side"],
            "strategy": r["strategy"],
            "amount": round(r["price"] * r["shares"], 2),
        })

    return trades


def get_position_cap() -> dict:
    open_count = state.count_open_positions("penny")
    target = STRATEGIES["penny"].max_positions
    max_per_event = STRATEGIES["penny"].max_per_event

    return {
        "open": open_count,
        "pending": 0,
        "remaining": max(0, target - open_count),
        "target": target,
        "max_per_event": max_per_event,
        "opened": open_count,
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/portfolio")
def api_portfolio():
    data = get_portfolio_summary()
    return jsonify(data)


@app.route("/api/positions")
def api_positions():
    return jsonify(get_open_positions())


@app.route("/api/trades")
def api_trades():
    return jsonify(get_recent_trades())


@app.route("/api/cap")
def api_cap():
    return jsonify(get_position_cap())


# ─── HTML Template ───────────────────────────────────────────────────────────

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Penny-Bot Dashboard</title>
    <style>
        :root {
            --bg: #f0ebe3;
            --card-bg: #f7f4ef;
            --card-border: #e8e4dc;
            --text: #2b2520;
            --text-muted: #8b8378;
            --text-mono: #5a5248;
            --green: #3d6b4f;
            --green-light: #e8f0eb;
            --badge-bg: #e8dcc8;
            --badge-text: #6b5a4a;
            --shadow: 0 2px 8px rgba(43, 37, 32, 0.08);
            --radius: 16px;
            --radius-sm: 10px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 24px 32px;
            line-height: 1.5;
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 28px;
        }
        .header h1 { font-size: 32px; font-weight: 800; letter-spacing: -0.5px; }
        .header-right { display: flex; align-items: center; gap: 12px; }
        .socket-status {
            display: flex; align-items: center; gap: 8px;
            font-size: 13px; color: var(--green);
            background: var(--green-light);
            padding: 6px 14px; border-radius: 20px; font-weight: 500;
        }
        .socket-dot {
            width: 8px; height: 8px;
            background: var(--green); border-radius: 50%;
            animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 14px;
            margin-bottom: 20px;
        }
        .metric-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 18px 16px;
            box-shadow: var(--shadow);
        }
        .metric-label {
            font-size: 10px; text-transform: uppercase;
            color: var(--text-muted); letter-spacing: 1px;
            font-weight: 600; margin-bottom: 10px;
        }
        .metric-value { font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
        .metric-sub { font-size: 11px; color: var(--text-muted); margin-top: 6px; }
        .positive { color: var(--green); }
        .negative { color: #a84438; }

        .cap-bar {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 20px 24px;
            margin-bottom: 24px;
            box-shadow: var(--shadow);
        }
        .cap-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
        .cap-title { font-weight: 700; font-size: 15px; }
        .cap-values { font-size: 13px; color: var(--text-muted); margin-bottom: 12px; }
        .cap-visual {
            height: 48px;
            background: linear-gradient(90deg,
                rgba(61,107,79,0.1) 0%,
                rgba(61,107,79,0.3) 30%,
                rgba(61,107,79,0.2) 70%,
                rgba(61,107,79,0.1) 100%
            );
            border-radius: 8px;
            filter: blur(8px);
        }

        .main-grid {
            display: grid;
            grid-template-columns: 1fr 380px;
            gap: 20px;
        }

        .positions-panel, .trades-panel {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 20px 24px;
            box-shadow: var(--shadow);
        }
        .trades-panel { max-height: 700px; overflow-y: auto; }

        .panel-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 18px; padding-bottom: 14px;
            border-bottom: 1px solid var(--card-border);
        }
        .panel-title { font-weight: 700; font-size: 15px; }
        .sort-info { font-size: 12px; color: var(--text-muted); }

        table { width: 100%; border-collapse: collapse; }
        th {
            text-align: left; font-size: 10px; text-transform: uppercase;
            color: var(--text-muted); letter-spacing: 0.8px; font-weight: 600;
            padding: 10px 8px; border-bottom: 1px solid var(--card-border);
        }
        td { padding: 16px 8px; border-bottom: 1px solid var(--card-border); font-size: 13px; vertical-align: top; }
        tr:last-child td { border-bottom: none; }

        .market-cell { min-width: 280px; }
        .market-name { font-weight: 600; margin-bottom: 4px; line-height: 1.4; }
        .market-slug { font-family: monospace; font-size: 11px; color: var(--text-mono); word-break: break-all; }
        .side-badge {
            display: inline-block; padding: 3px 10px; border-radius: 12px;
            font-size: 11px; font-weight: 600;
            background: var(--badge-bg); color: var(--badge-text);
        }
        .pnl-cell { min-width: 90px; }
        .pnl-value { font-weight: 600; color: var(--green); }
        .pnl-pct { font-size: 11px; color: var(--green); margin-top: 2px; }
        .pnl-value.negative, .pnl-pct.negative { color: #a84438; }

        .trade-item {
            background: #fff; border: 1px solid var(--card-border);
            border-radius: var(--radius-sm); padding: 14px 16px; margin-bottom: 12px;
        }
        .trade-item:last-child { margin-bottom: 0; }
        .trade-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
        .trade-action { font-weight: 700; font-size: 14px; text-transform: uppercase; }
        .trade-time { font-size: 11px; color: var(--text-muted); }
        .trade-market { font-family: monospace; font-size: 11px; color: var(--text-mono); margin-bottom: 8px; word-break: break-all; }
        .trade-details { font-size: 12px; color: var(--text-muted); }
        .trade-details strong { color: var(--text); font-weight: 600; }

        .trades-panel::-webkit-scrollbar { width: 6px; }
        .trades-panel::-webkit-scrollbar-track { background: transparent; }
        .trades-panel::-webkit-scrollbar-thumb { background: var(--card-border); border-radius: 3px; }

        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); font-size: 14px; }

        @media (max-width: 1400px) {
            .metrics-grid { grid-template-columns: repeat(4, 1fr); }
            .main-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Penny-Bot</h1>
        <div class="header-right">
            <div class="socket-status">
                <div class="socket-dot"></div>
                <span>live</span>
            </div>
        </div>
    </div>

    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-label">Monitored</div>
            <div class="metric-value" id="monitored">--</div>
            <div class="metric-sub">markets in cache</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Open Positions</div>
            <div class="metric-value" id="open-positions">--</div>
            <div class="metric-sub" id="position-sync">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Cash</div>
            <div class="metric-value" id="cash">--</div>
            <div class="metric-sub">simulated</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Portfolio</div>
            <div class="metric-value" id="portfolio">--</div>
            <div class="metric-sub" id="portfolio-sub">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Session PnL</div>
            <div class="metric-value positive" id="session-pnl">--</div>
            <div class="metric-sub" id="session-pnl-sub">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Win Rate</div>
            <div class="metric-value" id="win-rate">--</div>
            <div class="metric-sub" id="win-rate-sub">closed positions</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Last Error</div>
            <div class="metric-value" id="last-error">--</div>
            <div class="metric-sub">--</div>
        </div>
    </div>

    <div class="cap-bar">
        <div class="cap-header">
            <div class="cap-title">Position Cap</div>
            <div class="cap-status" style="font-size:12px;color:var(--text-muted)">penny strategy</div>
        </div>
        <div class="cap-values" id="cap-values">Loading...</div>
        <div class="cap-visual"></div>
    </div>

    <div class="main-grid">
        <div class="positions-panel">
            <div class="panel-header">
                <div class="panel-title">Open Positions</div>
                <div class="sort-info">sorted by PnL%</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th class="market-cell">Market</th>
                        <th>Side</th>
                        <th>Shares</th>
                        <th>Avg Paid</th>
                        <th>Current</th>
                        <th>Value</th>
                        <th class="pnl-cell">PnL</th>
                        <th class="pnl-cell">Pot. Win</th>
                    </tr>
                </thead>
                <tbody id="positions-table">
                    <tr><td colspan="8" class="empty-state">Loading...</td></tr>
                </tbody>
            </table>
        </div>

        <div class="trades-panel">
            <div class="panel-header">
                <div class="panel-title">Recent Trades</div>
                <div class="sort-info">trade ledger tail</div>
            </div>
            <div id="trades-list">
                <div class="empty-state">Loading...</div>
            </div>
        </div>
    </div>

    <script>
        async function fetchAll() {
            try {
                const [portfolio, positions, trades, cap] = await Promise.all([
                    fetch('/api/portfolio').then(r => r.json()),
                    fetch('/api/positions').then(r => r.json()),
                    fetch('/api/trades').then(r => r.json()),
                    fetch('/api/cap').then(r => r.json()),
                ]);
                updateMetrics(portfolio);
                updatePositions(positions);
                updateTrades(trades);
                updateCap(cap);
                document.getElementById('last-error').textContent = 'none';
            } catch (e) {
                document.getElementById('last-error').textContent = 'API Error';
                document.getElementById('last-error').classList.add('negative');
            }
        }

        function updateMetrics(data) {
            document.getElementById('monitored').textContent = data.monitored ?? '--';
            document.getElementById('open-positions').textContent = data.open_positions;
            document.getElementById('position-sync').textContent = `closed: ${data.closed_positions}`;
            document.getElementById('cash').textContent = '$' + fmt(data.cash);
            document.getElementById('portfolio').textContent = '$' + fmt(data.portfolio_value);
            document.getElementById('portfolio-sub').textContent = `positions $${fmt(data.positions_value)}`;
            const pnlEl = document.getElementById('session-pnl');
            pnlEl.textContent = (data.session_pnl >= 0 ? '+' : '') + '$' + fmt(data.session_pnl);
            pnlEl.className = 'metric-value ' + (data.session_pnl >= 0 ? 'positive' : 'negative');
            document.getElementById('win-rate').textContent = (data.win_rate * 100).toFixed(1) + '%';
        }

        function updatePositions(positions) {
            const tbody = document.getElementById('positions-table');
            if (!positions.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No open positions</td></tr>';
                return;
            }
            tbody.innerHTML = positions.map(p => `
                <tr>
                    <td class="market-cell">
                        <div class="market-name">${esc(p.question)}</div>
                        <div class="market-slug">${esc(p.slug)}</div>
                    </td>
                    <td><span class="side-badge">${p.side}</span></td>
                    <td>${p.shares.toFixed(2)}</td>
                    <td>$${p.entry_price.toFixed(4)}</td>
                    <td>$${p.current_price.toFixed(4)}</td>
                    <td>$${p.market_value.toFixed(2)}</td>
                    <td class="pnl-cell">
                        <div class="pnl-value ${p.pnl < 0 ? 'negative' : ''}">$${p.pnl.toFixed(2)}</div>
                        <div class="pnl-pct ${p.pnl < 0 ? 'negative' : ''}">${p.pnl >= 0 ? '+' : ''}${p.pnl_pct.toFixed(2)}%</div>
                    </td>
                    <td class="pnl-cell">
                        <div class="pnl-value">$${p.potential_win.toFixed(2)}</div>
                        <div class="pnl-pct">+${p.potential_win_pct.toFixed(2)}%</div>
                    </td>
                </tr>
            `).join('');
        }

        function updateTrades(trades) {
            const el = document.getElementById('trades-list');
            if (!trades.length) { el.innerHTML = '<div class="empty-state">No recent trades</div>'; return; }
            el.innerHTML = trades.map(t => `
                <div class="trade-item">
                    <div class="trade-header">
                        <div class="trade-action">${t.action}</div>
                        <div class="trade-time">${fmtTime(t.timestamp)}</div>
                    </div>
                    <div class="trade-market">${t.side} | ${esc(t.slug)}</div>
                    <div class="trade-details">
                        <strong>Amount:</strong> $${t.amount.toFixed(2)}
                        <strong>Price:</strong> $${t.price.toFixed(4)}
                        ${t.reason ? `<strong>Reason:</strong> ${t.reason}` : ''}
                    </div>
                </div>
            `).join('');
        }

        function updateCap(cap) {
            document.getElementById('cap-values').textContent =
                `Open ${cap.open} | Remaining ${cap.remaining} | Target ${cap.target} | Max/Event ${cap.max_per_event}`;
        }

        function fmt(n) { return n.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}); }
        function fmtTime(iso) { return new Date(iso).toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true}); }
        function esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

        fetchAll();
        setInterval(fetchAll, 5000);
    </script>
</body>
</html>
"""


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Penny-Bot Dashboard on http://localhost:5000")
    logger.info("Database: %s", DB_PATH)
    app.run(host="127.0.0.1", port=5000, debug=False)
