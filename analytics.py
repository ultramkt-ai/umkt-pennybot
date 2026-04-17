"""
analytics.py — Métricas, comparações, log completo, e exports.

Responsabilidades:
  1. Métricas por estratégia (penny vs NO) e por categoria
  2. EV real vs teórico
  3. Win rate, profit factor, tempo médio de hold, concentração
  4. Log completo de todas as transações (CSV + JSON)
  5. Relatório consolidado para Telegram daily digest

Todas as queries rodam direto no SQLite — sem ORM, sem dependência extra.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from config import STRATEGIES, DRAWDOWN_ALERT_THRESHOLD
from state import StateManager


# ─── Métricas ────────────────────────────────────────────────────────────────

@dataclass
class StrategyMetrics:
    """Métricas de uma estratégia (ou de uma categoria dentro de uma estratégia)."""

    name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_invested: float = 0.0
    roi: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0       # gross_profit / gross_loss
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_hold_hours: float = 0.0
    open_positions: int = 0
    unrealized_pnl: float = 0.0

    # EV tracking
    ev_theoretical: float = 0.0      # EV da estratégia (do config)
    ev_realized: float = 0.0         # EV real observado


@dataclass
class PortfolioReport:
    """Relatório consolidado do portfolio."""

    generated_at: str = ""
    overall: StrategyMetrics = None
    by_strategy: dict[str, StrategyMetrics] = field(default_factory=dict)
    by_category: dict[str, StrategyMetrics] = field(default_factory=dict)
    by_exit_reason: dict[str, int] = field(default_factory=dict)
    drawdown_alert: bool = False
    max_drawdown_pct: float = 0.0


# ─── Queries ─────────────────────────────────────────────────────────────────

class Analytics:
    """Calcula métricas a partir do state. Gera exports."""

    def __init__(self, state: StateManager, export_dir: str = "data/exports"):
        self.state = state
        self.export_dir = export_dir
        Path(export_dir).mkdir(parents=True, exist_ok=True)

    # ─── Métricas ────────────────────────────────────────────────────────

    def compute_metrics(
        self,
        strategy: str | None = None,
        category: str | None = None,
    ) -> StrategyMetrics:
        """
        Calcula métricas para um filtro (strategy e/ou category).
        Se ambos None, retorna métricas globais.
        """
        closed = self.state.get_all_positions(
            strategy=strategy, status="closed", category=category, limit=10000
        )
        resolved = self.state.get_all_positions(
            strategy=strategy, status="resolved", category=category, limit=10000
        )
        all_done = closed + resolved

        open_pos = [
            p for p in self.state.get_open_positions(strategy=strategy)
            if category is None or p.get("category") == category
        ]

        name = strategy or category or "overall"
        m = StrategyMetrics(name=name)
        m.total_trades = len(all_done)
        m.open_positions = len(open_pos)

        if not all_done:
            return m

        # Win/loss
        wins = [p for p in all_done if (p.get("pnl") or 0) > 0]
        losses = [p for p in all_done if (p.get("pnl") or 0) <= 0]
        m.wins = len(wins)
        m.losses = len(losses)
        m.win_rate = m.wins / m.total_trades if m.total_trades > 0 else 0.0

        # PnL
        pnls = [(p.get("pnl") or 0) for p in all_done]
        m.total_pnl = round(sum(pnls), 4)
        m.total_invested = round(sum(p.get("cost", 0) for p in all_done), 4)
        m.roi = round(m.total_pnl / m.total_invested, 4) if m.total_invested > 0 else 0.0

        # Avg win/loss
        win_pnls = [p.get("pnl", 0) for p in wins]
        loss_pnls = [p.get("pnl", 0) for p in losses]
        m.avg_win = round(sum(win_pnls) / len(win_pnls), 4) if win_pnls else 0.0
        m.avg_loss = round(sum(loss_pnls) / len(loss_pnls), 4) if loss_pnls else 0.0

        # Profit factor
        gross_profit = sum(pnl for pnl in pnls if pnl > 0)
        gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
        m.profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")

        # Best/worst
        m.best_trade = round(max(pnls), 4)
        m.worst_trade = round(min(pnls), 4)

        # Avg hold time
        hold_hours = []
        for p in all_done:
            opened = p.get("opened_at", "")
            closed_at = p.get("closed_at", "")
            if opened and closed_at:
                try:
                    t_open = datetime.fromisoformat(opened)
                    t_close = datetime.fromisoformat(closed_at)
                    hours = (t_close - t_open).total_seconds() / 3600
                    hold_hours.append(hours)
                except (ValueError, TypeError):
                    pass
        m.avg_hold_hours = round(sum(hold_hours) / len(hold_hours), 2) if hold_hours else 0.0

        # Unrealized PnL
        m.unrealized_pnl = round(
            sum(
                ((p.get("current_price") or p["entry_price"]) - p["entry_price"]) * p["shares"]
                for p in open_pos
            ),
            4,
        )

        # EV teórico (do config)
        if strategy and strategy in STRATEGIES:
            params = STRATEGIES[strategy]
            m.ev_theoretical = round(
                params.base_win_rate * (1.0 - params.max_price)
                - (1.0 - params.base_win_rate) * params.max_price,
                4,
            )

        # EV realizado (média de PnL / custo por trade)
        if m.total_invested > 0:
            m.ev_realized = round(m.total_pnl / m.total_trades, 4) if m.total_trades > 0 else 0.0

        return m

    def compute_full_report(self) -> PortfolioReport:
        """Gera relatório completo: overall + por estratégia + por categoria + por exit_reason."""
        report = PortfolioReport()
        report.generated_at = datetime.now(timezone.utc).isoformat()

        # Overall
        report.overall = self.compute_metrics()

        # Por estratégia
        for name in STRATEGIES:
            report.by_strategy[name] = self.compute_metrics(strategy=name)

        # Por categoria (das posições que existem)
        categories = self._get_distinct_categories()
        for cat in categories:
            report.by_category[cat] = self.compute_metrics(category=cat)

        # Por exit_reason
        report.by_exit_reason = self._count_exit_reasons()

        # Drawdown check
        if report.overall.total_invested > 0:
            report.max_drawdown_pct = abs(
                min(report.overall.worst_trade, 0)
                / report.overall.total_invested
            )
            report.drawdown_alert = report.max_drawdown_pct > DRAWDOWN_ALERT_THRESHOLD

        return report

    def _get_distinct_categories(self) -> list[str]:
        """Retorna categorias únicas de todas as posições."""
        with self.state._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM positions WHERE category IS NOT NULL"
            ).fetchall()
        return [r["category"] for r in rows]

    def _count_exit_reasons(self) -> dict[str, int]:
        """Conta posições por exit_reason."""
        with self.state._connect() as conn:
            rows = conn.execute(
                """SELECT exit_reason, COUNT(*) as cnt
                   FROM positions
                   WHERE status IN ('closed', 'resolved') AND exit_reason IS NOT NULL
                   GROUP BY exit_reason"""
            ).fetchall()
        return {r["exit_reason"]: r["cnt"] for r in rows}

    # ─── Log completo de transações ──────────────────────────────────────

    def get_full_trade_log(self) -> list[dict]:
        """
        Retorna log completo de todas as posições (abertas + fechadas)
        com todos os campos. Ordenado por opened_at.

        Cada item inclui: id, market_id, strategy, side, entry_price,
        exit_price, shares, cost, pnl, exit_reason, category, opened_at,
        closed_at, market_question, hold_hours, pnl_pct.
        """
        all_positions = self.state.get_all_positions(limit=100000)

        log = []
        for p in all_positions:
            hold_hours = None
            if p.get("opened_at") and p.get("closed_at"):
                try:
                    t_open = datetime.fromisoformat(p["opened_at"])
                    t_close = datetime.fromisoformat(p["closed_at"])
                    hold_hours = round((t_close - t_open).total_seconds() / 3600, 2)
                except (ValueError, TypeError):
                    pass

            cost = p.get("cost") or 0
            pnl = p.get("pnl") or 0
            pnl_pct = round(pnl / cost, 4) if cost > 0 else 0.0

            log.append({
                "position_id": p["id"],
                "market_id": p["market_id"],
                "condition_id": p["condition_id"],
                "event_id": p["event_id"],
                "strategy": p["strategy"],
                "side": p["side"],
                "category": p.get("category", "other"),
                "market_question": p.get("market_question", ""),
                "status": p["status"],
                "entry_price": p["entry_price"],
                "exit_price": p.get("exit_price"),
                "current_price": p.get("current_price"),
                "shares": p["shares"],
                "cost": cost,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "exit_reason": p.get("exit_reason"),
                "target_exit": p.get("target_exit"),
                "stop_price": p.get("stop_price"),
                "opened_at": p.get("opened_at"),
                "closed_at": p.get("closed_at"),
                "hold_hours": hold_hours,
            })

        # Ordenar por data de abertura (mais antiga primeiro)
        log.sort(key=lambda x: x.get("opened_at") or "")
        return log

    def get_trades_history_log(self) -> list[dict]:
        """
        Retorna trades_history completo (open + close actions, com timestamps).
        Útil para reconstruir sequência exata de eventos.
        """
        with self.state._connect() as conn:
            rows = conn.execute(
                """SELECT th.*, p.market_id, p.strategy, p.side, p.market_question
                   FROM trades_history th
                   JOIN positions p ON th.position_id = p.id
                   ORDER BY th.timestamp ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── Exports ─────────────────────────────────────────────────────────

    def export_trade_log_csv(self, filename: str | None = None) -> str:
        """Exporta log completo de posições como CSV. Retorna filepath."""
        if filename is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"trade_log_{ts}.csv"

        filepath = os.path.join(self.export_dir, filename)
        log = self.get_full_trade_log()

        if not log:
            return filepath

        fieldnames = list(log[0].keys())
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(log)

        return filepath

    def export_trade_log_json(self, filename: str | None = None) -> str:
        """Exporta log completo de posições como JSON. Retorna filepath."""
        if filename is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"trade_log_{ts}.json"

        filepath = os.path.join(self.export_dir, filename)
        log = self.get_full_trade_log()

        with open(filepath, "w") as f:
            json.dump(log, f, indent=2, default=str)

        return filepath

    def export_report_json(self, filename: str | None = None) -> str:
        """Exporta relatório consolidado como JSON. Retorna filepath."""
        if filename is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"report_{ts}.json"

        filepath = os.path.join(self.export_dir, filename)
        report = self.compute_full_report()

        data = {
            "generated_at": report.generated_at,
            "overall": asdict(report.overall),
            "by_strategy": {k: asdict(v) for k, v in report.by_strategy.items()},
            "by_category": {k: asdict(v) for k, v in report.by_category.items()},
            "by_exit_reason": report.by_exit_reason,
            "drawdown_alert": report.drawdown_alert,
            "max_drawdown_pct": report.max_drawdown_pct,
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        return filepath

    # ─── Formatação para Telegram ────────────────────────────────────────

    def format_daily_digest(self) -> str:
        """
        Gera texto formatado para o Telegram daily digest.
        Conciso mas completo — uma mensagem que conta o estado do portfolio.
        """
        report = self.compute_full_report()
        o = report.overall

        lines = [
            "📊 *Daily Digest*",
            "",
            f"💰 PnL total: ${o.total_pnl:+,.2f} (ROI: {o.roi:.1%})",
            f"📈 Posições: {o.open_positions} abertas, {o.total_trades} fechadas",
            f"🎯 Win rate: {o.win_rate:.1%} ({o.wins}W / {o.losses}L)",
            f"⚖️ Profit factor: {o.profit_factor:.2f}",
            f"📉 Unrealized: ${o.unrealized_pnl:+,.2f}",
        ]

        if o.total_trades > 0:
            lines.append(f"🏆 Melhor: ${o.best_trade:+,.2f} | Pior: ${o.worst_trade:+,.2f}")
            lines.append(f"⏱️ Hold médio: {o.avg_hold_hours:.1f}h")

        # Por estratégia
        for name, m in report.by_strategy.items():
            if m.total_trades > 0 or m.open_positions > 0:
                lines.append("")
                lines.append(f"*{name}*: {m.wins}W/{m.losses}L "
                             f"${m.total_pnl:+,.2f} (WR={m.win_rate:.0%})")
                if m.ev_theoretical != 0:
                    lines.append(
                        f"  EV teórico: ${m.ev_theoretical:.4f} | "
                        f"EV real: ${m.ev_realized:.4f}"
                    )

        # Exit reasons
        if report.by_exit_reason:
            reasons = ", ".join(
                f"{r}={c}" for r, c in sorted(report.by_exit_reason.items())
            )
            lines.append(f"\n🚪 Exits: {reasons}")

        # Drawdown alert
        if report.drawdown_alert:
            lines.append(f"\n⚠️ *DRAWDOWN ALERT*: {report.max_drawdown_pct:.1%}")

        return "\n".join(lines)
