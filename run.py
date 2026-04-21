"""
run.py — CLI principal do Polymarket Probability Bot.

Comandos:
  python run.py scan       → Scan único (busca mercados + filtra + entra)
  python run.py monitor    → Um ciclo do monitor (check preços + TP/SL)
  python run.py report     → Mostra relatório no terminal
  python run.py digest     → Envia daily digest no Telegram
  python run.py export     → Exporta trade log (CSV + JSON) e relatório
  python run.py status     → Status rápido do portfolio
  python run.py loop       → Ciclo contínuo (scan + monitor + digest)

Cada comando é independente — pode rodar manualmente ou via cron.
O "loop" roda tudo junto com os intervalos do config.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from config import (
    STRATEGIES,
    SCAN_INTERVAL_SECONDS,
    MONITOR_INTERVAL_SECONDS,
    MODE,
    ALLOWED_CATEGORIES,
)
from state import StateManager
from scanner import scan_allowed_categories
from gamma_client import fetch_event_by_id, normalize_market, GammaAPIError
from filters import filter_markets
from strategy import generate_signals, calculate_targets
from paper_engine import PaperEngine
from monitor import Monitor
from analytics import Analytics
from telegram_bot import TelegramNotifier


logger = logging.getLogger("run")


# ─── Setup ───────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Reduzir ruído de requests/urllib3
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def create_components() -> tuple[
    StateManager, PaperEngine, Monitor, Analytics, TelegramNotifier
]:
    state = StateManager()
    engine = PaperEngine(state, mode=MODE)
    monitor = Monitor(state, engine)
    analytics = Analytics(state)
    notifier = TelegramNotifier()
    return state, engine, monitor, analytics, notifier


# ─── Scan: buscar → filtrar → decidir → entrar ─────────────────────────────

def cmd_scan(
    state: StateManager,
    engine: PaperEngine,
    notifier: TelegramNotifier,
    bankroll: float = 1000.0,
) -> int:
    """
    Pipeline completo de scan:
      1. Buscar mercados da Gamma API por tag
      2. Para cada estratégia: filtrar elegíveis → gerar sinais → executar entradas

    Retorna total de novas posições abertas.
    """
    logger.info("=== SCAN START ===")
    logger.info("Mode: %s | Categories: %s", MODE, list(ALLOWED_CATEGORIES))

    # 1. Scan da Gamma API
    scan_result = scan_allowed_categories(state)
    logger.info(scan_result.summary())

    # 2. Buscar mercados do cache (inclui os recém-escaneados)
    cached_markets = state.get_active_markets()
    logger.info("Mercados no cache: %d", len(cached_markets))
    market_map = {m.get("market_id"): m for m in cached_markets}

    state.record_ledger_event(
        event_type="scan_cycle",
        reason="scan_start",
        source="run.cmd_scan",
        payload={
            "mode": MODE,
            "bankroll": bankroll,
            "categories": list(ALLOWED_CATEGORIES),
            "markets_in_cache": len(cached_markets),
            "scan_summary": scan_result.summary(),
            "scan_errors": scan_result.errors,
            "scan_by_category": scan_result.by_category,
        },
    )

    total_entries = 0

    # 3. Para cada estratégia ativa
    for strategy_name, strategy in STRATEGIES.items():
        logger.info("--- Estratégia: %s ---", strategy_name)

        open_positions_before = state.count_open_positions(strategy_name)
        open_invested_before = state.get_open_invested(strategy_name)
        available_capital = max(0.0, bankroll - open_invested_before)
        state.set_strategy_runtime(
            strategy_name,
            bankroll,
            payload={
                "open_positions_before": open_positions_before,
                "open_invested_before": round(open_invested_before, 4),
                "available_capital_before": round(available_capital, 4),
            },
        )

        # Filtrar elegíveis
        eligible, summaries = filter_markets(cached_markets, strategy, state)
        rejected = [s for s in summaries if not s.passed]
        passed = [s for s in summaries if s.passed]
        logger.info(
            "  Filtro: %d elegíveis, %d rejeitados (de %d mercados)",
            len(eligible), len(rejected), len(cached_markets),
        )

        state.record_ledger_event(
            event_type="strategy_scan_summary",
            strategy=strategy_name,
            reason="filter_complete",
            source="run.cmd_scan",
            payload={
                "bankroll": bankroll,
                "eligible_count": len(eligible),
                "rejected_count": len(rejected),
                "cached_markets": len(cached_markets),
                "max_positions": strategy.max_positions,
                "open_positions_before": open_positions_before,
                "open_invested_before": round(open_invested_before, 4),
                "available_capital_before": round(available_capital, 4),
                "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
            },
        )

        rejection_counts: dict[str, int] = {}
        rejection_examples: dict[str, dict] = {}
        for summary in rejected:
            key = summary.failed_at or "unknown_filter"
            rejection_counts[key] = rejection_counts.get(key, 0) + 1
            rejection_examples.setdefault(
                key,
                {
                    "market_id": summary.market_id,
                    "question": summary.question,
                    "reason": summary.reason,
                },
            )

        if rejected:
            state.record_ledger_event(
                event_type="market_rejections_aggregate",
                strategy=strategy_name,
                reason="filter_rejections_aggregated",
                source="run.cmd_scan",
                payload={
                    "total_rejected": len(rejected),
                    "rejection_counts": rejection_counts,
                    "examples": rejection_examples,
                },
            )

        if not eligible:
            continue

        # Gerar sinais (EV > 0, ordenados por EV%)
        slots = strategy.max_positions - open_positions_before
        if slots <= 0 or available_capital <= 0:
            reason = "no_slots_available" if slots <= 0 else "no_capital_available"
            logger.info(
                "  Sem capacidade disponível (slots=%d, capital_disponivel=$%.2f)",
                slots,
                available_capital,
            )
            state.record_ledger_event(
                event_type="market_skipped_capacity_aggregate",
                strategy=strategy_name,
                reason=reason,
                source="run.cmd_scan",
                payload={
                    "total_skipped": len(passed),
                    "max_positions": strategy.max_positions,
                    "available_capital": round(available_capital, 4),
                    "open_invested_before": round(open_invested_before, 4),
                    "example_market_ids": [summary.market_id for summary in passed[:20]],
                },
            )
            continue

        ranked_signals = generate_signals(
            eligible, strategy,
            bankroll=bankroll,
            max_signals=None,
        )
        capital_limited_selection = []
        overflow_signals = []
        running_cost = 0.0
        for signal in ranked_signals:
            if len(capital_limited_selection) >= slots:
                overflow_signals.append(signal)
                continue
            next_cost = running_cost + signal.cost
            if next_cost <= available_capital + 1e-9:
                capital_limited_selection.append(signal)
                running_cost = next_cost
            else:
                overflow_signals.append(signal)

        selected_signals = capital_limited_selection
        logger.info(
            "  Sinais com EV > 0: %d (slots=%d, capital_disponivel=$%.2f, selecionados=%d, custo_selecionado=$%.2f)",
            len(ranked_signals), slots, available_capital, len(selected_signals), running_cost
        )

        tokenless_selected = [signal for signal in selected_signals if not signal.token_id]
        if tokenless_selected:
            logger.error(
                "  %d sinais selecionados sem token_id. Entradas serão bloqueadas para evitar posições sem monitoramento.",
                len(tokenless_selected),
            )
            tokenless_events = []
            for signal in tokenless_selected:
                tokenless_events.append({
                    "event_type": "signal_blocked_missing_token",
                    "strategy": strategy_name,
                    "market_id": signal.market_id,
                    "event_id": signal.event_id,
                    "condition_id": signal.condition_id,
                    "side": signal.side,
                    "position_status": "blocked",
                    "price": signal.entry_price,
                    "shares": signal.shares,
                    "notional": signal.cost,
                    "reason": "missing_token_id",
                    "source": "run.cmd_scan",
                    "payload": {"signal": asdict(signal)},
                })
            state.record_ledger_events(tokenless_events)
            selected_signals = [signal for signal in selected_signals if signal.token_id]

        if ranked_signals:
            state.record_ledger_event(
                event_type="signal_generation_aggregate",
                strategy=strategy_name,
                reason="signals_ranked",
                source="run.cmd_scan",
                payload={
                    "ranked_signals": len(ranked_signals),
                    "selected_signals": len(selected_signals),
                    "overflow_signals": len(overflow_signals),
                    "slots": slots,
                    "available_capital": round(available_capital, 4),
                    "selected_examples": [asdict(signal) for signal in selected_signals[:10]],
                    "overflow_examples": [asdict(signal) for signal in overflow_signals[:10]],
                },
            )

        if not selected_signals:
            continue

        # Executar entradas
        results = engine.execute_entries(selected_signals)
        entries = [r for r in results if r.success]
        total_entries += len(entries)

        execution_events = []
        for signal, result in zip(selected_signals, results):
            execution_events.append({
                "position_id": result.position_id,
                "event_type": "entry_execution_result",
                "strategy": strategy_name,
                "market_id": signal.market_id,
                "event_id": signal.event_id,
                "condition_id": signal.condition_id,
                "side": signal.side,
                "position_status": "open" if result.success else "rejected",
                "price": signal.entry_price,
                "shares": signal.shares,
                "notional": signal.cost,
                "reason": "executed" if result.success else "execution_failed",
                "source": "run.cmd_scan",
                "payload": {
                    "signal": asdict(signal),
                    "result_message": result.message,
                    "success": result.success,
                },
            })
        state.record_ledger_events(execution_events)

        logger.info("  Entradas executadas: %d", len(entries))

        # Notificar Telegram
        notifier.notify_entries(entries)

    # Notificar resumo do scan
    notifier.notify_scan(scan_result.summary(), total_entries)

    logger.info("=== SCAN END: %d novas posições ===", total_entries)
    return total_entries


# ─── Monitor: checar preços → TP/SL/resolução → exits ──────────────────────

def cmd_monitor(
    monitor: Monitor,
    notifier: TelegramNotifier,
) -> None:
    """Um ciclo do monitor."""
    logger.info("=== MONITOR CYCLE ===")
    result = monitor.run_cycle()
    logger.info(result.summary())

    # Notificar eventos
    if result.events or result.errors:
        notifier.notify_monitor_events(result)


# ─── Report: métricas no terminal ───────────────────────────────────────────

def cmd_report(analytics: Analytics) -> None:
    """Mostra relatório consolidado no terminal."""
    report = analytics.compute_full_report()
    o = report.overall

    print("\n" + "=" * 60)
    print("  POLYMARKET PROBABILITY BOT — REPORT")
    print("=" * 60)

    print(f"\n  Mode: {MODE}")
    print(f"  Generated: {report.generated_at}")

    print(f"\n  --- Overall ---")
    print(f"  Positions: {o.open_positions} open, {o.total_trades} closed")
    print(f"  Win rate:  {o.win_rate:.1%} ({o.wins}W / {o.losses}L)")
    print(f"  PnL:       ${o.total_pnl:+,.2f}")
    print(f"  Invested:  ${o.total_invested:,.2f}")
    print(f"  ROI:       {o.roi:.1%}")
    print(f"  Profit F:  {o.profit_factor:.2f}")
    print(f"  Best:      ${o.best_trade:+,.2f}")
    print(f"  Worst:     ${o.worst_trade:+,.2f}")
    print(f"  Avg hold:  {o.avg_hold_hours:.1f}h")
    print(f"  Unrealized:${o.unrealized_pnl:+,.2f}")

    for name, m in report.by_strategy.items():
        if m.total_trades > 0 or m.open_positions > 0:
            print(f"\n  --- {name} ---")
            print(f"  {m.wins}W/{m.losses}L  WR={m.win_rate:.0%}  "
                  f"PnL=${m.total_pnl:+,.2f}  ROI={m.roi:.1%}")
            print(f"  EV teórico: ${m.ev_theoretical:.4f}  "
                  f"EV real: ${m.ev_realized:.4f}")
            print(f"  Open: {m.open_positions}  Unrealized: ${m.unrealized_pnl:+,.2f}")

    if report.by_exit_reason:
        print(f"\n  --- Exit Reasons ---")
        for reason, count in sorted(report.by_exit_reason.items()):
            print(f"  {reason}: {count}")

    if report.by_category:
        print(f"\n  --- Por Categoria ---")
        for cat, m in sorted(report.by_category.items()):
            if m.total_trades > 0:
                print(f"  {cat}: {m.wins}W/{m.losses}L "
                      f"PnL=${m.total_pnl:+,.2f} WR={m.win_rate:.0%}")

    if report.drawdown_alert:
        print(f"\n  ⚠️  DRAWDOWN ALERT: {report.max_drawdown_pct:.1%}")

    print("\n" + "=" * 60)


# ─── Digest: enviar para Telegram ───────────────────────────────────────────

def cmd_digest(analytics: Analytics, notifier: TelegramNotifier) -> None:
    """Gera e envia daily digest para o Telegram."""
    text = analytics.format_daily_digest()
    print(text)
    sent = notifier.notify_daily_digest(text)
    if sent:
        logger.info("Daily digest enviado ao Telegram")
    else:
        logger.info("Daily digest não enviado (Telegram não configurado ou falhou)")


# ─── Export: CSV + JSON ─────────────────────────────────────────────────────

def cmd_export(analytics: Analytics) -> None:
    """Exporta trade log e relatório."""
    csv_path = analytics.export_trade_log_csv()
    json_path = analytics.export_trade_log_json()
    report_path = analytics.export_report_json()

    print(f"Trade log CSV:  {csv_path}")
    print(f"Trade log JSON: {json_path}")
    print(f"Report JSON:    {report_path}")
    logger.info("Exports gerados")


# ─── Status: resumo rápido ──────────────────────────────────────────────────

def cmd_status(state: StateManager) -> None:
    """Status rápido do portfolio."""
    stats = state.get_stats_summary()
    print(f"\nMode:     {MODE}")
    print(f"Open:     {stats['open_positions']} positions")
    print(f"Closed:   {stats['closed_positions']}")
    print(f"Win rate: {stats['win_rate']:.1%}")
    print(f"PnL:      ${stats['total_pnl']:+,.2f}")
    print(f"Invested: ${stats['total_invested']:,.2f}")
    print(f"ROI:      {stats['roi']:.1%}")


# ─── Repair: restaurar token_ids faltantes ─────────────────────────────────

def cmd_repair_tokens(state: StateManager) -> dict[str, int]:
    """
    Reconstroi token_ids faltantes das posições abertas usando a Gamma API.

    Fluxo:
      1. Carrega posições abertas sem token_id
      2. Busca os eventos de origem diretamente por event_id
      3. Normaliza e regrava mercados no cache com yes/no token ids
      4. Atualiza cada posição com o token correto para seu side
      5. Registra trilha de auditoria no ledger
    """
    logger.info("=== TOKEN REPAIR START ===")

    open_positions = state.get_open_positions()
    missing_positions = [pos for pos in open_positions if not pos.get("token_id")]
    summary = {
        "open_positions": len(open_positions),
        "missing_positions": len(missing_positions),
        "events_requested": 0,
        "markets_refreshed": 0,
        "positions_backfilled": 0,
        "positions_failed": 0,
    }

    state.record_ledger_event(
        event_type="token_repair_cycle",
        reason="start",
        source="run.cmd_repair_tokens",
        payload=summary,
    )

    if not missing_positions:
        logger.info("Nenhuma posição aberta está sem token_id")
        return summary

    positions_by_event: dict[str, list[dict]] = defaultdict(list)
    for pos in missing_positions:
        positions_by_event[pos.get("event_id", "")].append(pos)

    market_map: dict[str, dict] = {}
    for event_id, positions in positions_by_event.items():
        if not event_id:
            logger.error("Evento vazio em %d posições sem token_id", len(positions))
            for pos in positions:
                summary["positions_failed"] += 1
                state.record_ledger_event(
                    position_id=pos["id"],
                    event_type="position_token_backfill_failed",
                    strategy=pos["strategy"],
                    market_id=pos["market_id"],
                    event_id=pos.get("event_id"),
                    condition_id=pos.get("condition_id"),
                    side=pos["side"],
                    position_status=pos.get("status"),
                    reason="missing_event_id",
                    source="run.cmd_repair_tokens",
                    payload={"position": pos},
                )
            continue

        summary["events_requested"] += 1
        logger.info(
            "Recarregando evento %s para %d posições sem token_id",
            event_id,
            len(positions),
        )

        try:
            event = fetch_event_by_id(event_id)
        except GammaAPIError as e:
            logger.error("Falha ao buscar evento %s: %s", event_id, e)
            for pos in positions:
                summary["positions_failed"] += 1
                state.record_ledger_event(
                    position_id=pos["id"],
                    event_type="position_token_backfill_failed",
                    strategy=pos["strategy"],
                    market_id=pos["market_id"],
                    event_id=pos.get("event_id"),
                    condition_id=pos.get("condition_id"),
                    side=pos["side"],
                    position_status=pos.get("status"),
                    reason="event_fetch_failed",
                    source="run.cmd_repair_tokens",
                    payload={"error": str(e)},
                )
            continue

        raw_markets = event.get("markets") or []
        for raw_market in raw_markets:
            market = normalize_market(raw_market, parent_event=event)
            state.upsert_market(market)
            market_map[market["market_id"]] = market
            summary["markets_refreshed"] += 1

    for pos in missing_positions:
        market = market_map.get(pos["market_id"]) or state.get_cached_market(pos["market_id"])
        if market is None:
            summary["positions_failed"] += 1
            state.record_ledger_event(
                position_id=pos["id"],
                event_type="position_token_backfill_failed",
                strategy=pos["strategy"],
                market_id=pos["market_id"],
                event_id=pos.get("event_id"),
                condition_id=pos.get("condition_id"),
                side=pos["side"],
                position_status=pos.get("status"),
                reason="market_not_found_after_refresh",
                source="run.cmd_repair_tokens",
                payload={"position": pos},
            )
            continue

        token_id = market.get("yes_token_id") if pos["side"] == "YES" else market.get("no_token_id")
        if not token_id:
            summary["positions_failed"] += 1
            state.record_ledger_event(
                position_id=pos["id"],
                event_type="position_token_backfill_failed",
                strategy=pos["strategy"],
                market_id=pos["market_id"],
                event_id=pos.get("event_id"),
                condition_id=pos.get("condition_id"),
                side=pos["side"],
                position_status=pos.get("status"),
                reason="token_missing_after_refresh",
                source="run.cmd_repair_tokens",
                payload={
                    "position": pos,
                    "market": market,
                },
            )
            continue

        state.update_position_token_id(
            pos["id"],
            token_id,
            source="run.cmd_repair_tokens",
            payload={
                "market_question": pos.get("market_question"),
                "market_category": market.get("category"),
                "side": pos["side"],
                "event_id": pos.get("event_id"),
            },
        )
        summary["positions_backfilled"] += 1

    state.record_ledger_event(
        event_type="token_repair_cycle",
        reason="completed",
        source="run.cmd_repair_tokens",
        payload=summary,
    )

    logger.info(
        "=== TOKEN REPAIR END === open=%d missing=%d refreshed_markets=%d backfilled=%d failed=%d",
        summary["open_positions"],
        summary["missing_positions"],
        summary["markets_refreshed"],
        summary["positions_backfilled"],
        summary["positions_failed"],
    )
    print(summary)
    return summary


def cmd_repair_open_risk_params(state: StateManager) -> dict[str, int]:
    """
    Recalcula target/stop/bounce das posições abertas usando a lógica atual da estratégia.

    Serve para sanar posições legadas abertas com parâmetros incoerentes no banco,
    preservando trilha de auditoria por posição.
    """
    logger.info("=== RISK PARAM REPAIR START ===")
    open_positions = state.get_open_positions()
    summary = {
        "open_positions": len(open_positions),
        "positions_repaired": 0,
        "positions_skipped": 0,
        "positions_failed": 0,
    }

    state.record_ledger_event(
        event_type="risk_param_repair_cycle",
        reason="start",
        source="run.cmd_repair_open_risk_params",
        payload=summary,
    )

    for pos in open_positions:
        strategy = STRATEGIES.get(pos["strategy"])
        if strategy is None:
            summary["positions_failed"] += 1
            state.record_ledger_event(
                position_id=pos["id"],
                event_type="position_risk_param_repair_failed",
                strategy=pos["strategy"],
                market_id=pos["market_id"],
                event_id=pos.get("event_id"),
                condition_id=pos.get("condition_id"),
                side=pos["side"],
                position_status=pos.get("status"),
                reason="strategy_not_found",
                source="run.cmd_repair_open_risk_params",
                payload={"position": pos},
            )
            continue

        target_exit, stop_price = calculate_targets(pos["entry_price"], strategy)
        current_target = pos.get("target_exit")
        current_stop = pos.get("stop_price")
        current_bounce = pos.get("bounce_exit_pct")

        if (
            current_target == target_exit
            and current_stop == stop_price
            and current_bounce == strategy.bounce_exit_threshold
        ):
            summary["positions_skipped"] += 1
            continue

        state.update_position_risk_params(
            pos["id"],
            target_exit=target_exit,
            stop_price=stop_price,
            bounce_exit_pct=strategy.bounce_exit_threshold,
            source="run.cmd_repair_open_risk_params",
            payload={
                "entry_price": pos["entry_price"],
                "market_question": pos.get("market_question"),
                "strategy_name": strategy.name,
            },
        )
        summary["positions_repaired"] += 1

    state.record_ledger_event(
        event_type="risk_param_repair_cycle",
        reason="completed",
        source="run.cmd_repair_open_risk_params",
        payload=summary,
    )
    logger.info(
        "=== RISK PARAM REPAIR END === open=%d repaired=%d skipped=%d failed=%d",
        summary["open_positions"],
        summary["positions_repaired"],
        summary["positions_skipped"],
        summary["positions_failed"],
    )
    print(summary)
    return summary


# ─── Loop: ciclo contínuo ───────────────────────────────────────────────────

def cmd_loop(
    state: StateManager,
    engine: PaperEngine,
    monitor: Monitor,
    analytics: Analytics,
    notifier: TelegramNotifier,
    bankroll: float = 1000.0,
) -> None:
    """
    Ciclo contínuo:
      - Scan a cada SCAN_INTERVAL_SECONDS (1h)
      - Monitor a cada MONITOR_INTERVAL_SECONDS (5min)
      - Daily digest a cada 24h (às 00:00 UTC)

    Ctrl+C para parar.
    """
    # Graceful shutdown
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        logger.info("Sinal %d recebido — parando após o ciclo atual...", signum)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("=== LOOP START ===")
    logger.info("Mode: %s", MODE)
    logger.info("Scan interval: %ds", SCAN_INTERVAL_SECONDS)
    logger.info("Monitor interval: %ds", MONITOR_INTERVAL_SECONDS)
    logger.info("Categories: %s", list(ALLOWED_CATEGORIES))
    logger.info("Strategies: %s", list(STRATEGIES.keys()))

    last_scan = 0.0
    last_monitor = 0.0
    last_digest_day = ""

    # Scan inicial imediato
    try:
        cmd_scan(state, engine, notifier, bankroll)
        last_scan = time.monotonic()
    except Exception as e:
        logger.error("Erro no scan inicial: %s", e)
        notifier.notify_error(f"Erro no scan inicial: {e}")

    while running:
        now = time.monotonic()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Monitor
        if now - last_monitor >= MONITOR_INTERVAL_SECONDS:
            try:
                cmd_monitor(monitor, notifier)
                last_monitor = now
            except Exception as e:
                logger.error("Erro no monitor: %s", e)
                notifier.notify_error(f"Erro no monitor: {e}")

        # Scan
        if now - last_scan >= SCAN_INTERVAL_SECONDS:
            try:
                cmd_scan(state, engine, notifier, bankroll)
                last_scan = now
            except Exception as e:
                logger.error("Erro no scan: %s", e)
                notifier.notify_error(f"Erro no scan: {e}")

        # Daily digest (1x por dia, ao virar o dia UTC)
        if today != last_digest_day:
            try:
                cmd_digest(analytics, notifier)
                cleanup_summary = state.cleanup_ledger_events()
                logger.info("Ledger cleanup: %s", cleanup_summary)
                # Snapshot diário
                snap_path = state.save_snapshot()
                logger.info("Snapshot: %s", snap_path)
                last_digest_day = today
            except Exception as e:
                logger.error("Erro no digest: %s", e)

        # Dormir 30s entre checks (não precisa ser mais rápido)
        for _ in range(30):
            if not running:
                break
            time.sleep(1)

    # Shutdown limpo
    logger.info("=== LOOP STOP ===")
    logger.info("Salvando snapshot final...")
    try:
        state.save_snapshot()
    except Exception as e:
        logger.error("Erro ao salvar snapshot: %s", e)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket Probability Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Comandos:
  scan          Busca mercados, filtra, gera sinais, executa entradas
  monitor       Um ciclo de monitoramento (preços, TP/SL, resolução)
  report        Relatório consolidado no terminal
  digest        Envia daily digest no Telegram
  export        Exporta trade log (CSV + JSON) e relatório
  status        Status rápido do portfolio
  repair-tokens Reconstroi token_ids faltantes das posições abertas
  repair-risk   Recalcula target/stop/bounce das posições abertas
  cleanup       Remove eventos volumosos do ledger; opcionalmente faz VACUUM
  loop          Ciclo contínuo (scan + monitor + digest)

Exemplos:
  python run.py scan                    # Scan único
  python run.py loop --verbose          # Loop com logs detalhados
  python run.py report                  # Relatório no terminal
  python run.py export                  # Exportar dados
  BOT_MODE=live python run.py scan      # Modo live (requer config)
""",
    )
    parser.add_argument(
        "command",
        choices=["scan", "monitor", "report", "digest", "export", "status", "repair-tokens", "repair-risk", "cleanup", "loop"],
        help="Comando a executar",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Logs detalhados (DEBUG)",
    )
    parser.add_argument(
        "--bankroll", "-b",
        type=float,
        default=1000.0,
        help="Bankroll para sizing (default: $1000)",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Após cleanup do ledger, roda VACUUM para devolver espaço ao disco",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    state, engine, monitor, analytics, notifier = create_components()

    def cmd_cleanup() -> None:
        summary = state.cleanup_ledger_events()
        print(summary)
        if args.vacuum:
            with state._connect() as conn:
                conn.execute("VACUUM")
            print({"vacuum": "completed"})

    commands = {
        "scan": lambda: cmd_scan(state, engine, notifier, args.bankroll),
        "monitor": lambda: cmd_monitor(monitor, notifier),
        "report": lambda: cmd_report(analytics),
        "digest": lambda: cmd_digest(analytics, notifier),
        "export": lambda: cmd_export(analytics),
        "status": lambda: cmd_status(state),
        "repair-tokens": lambda: cmd_repair_tokens(state),
        "repair-risk": lambda: cmd_repair_open_risk_params(state),
        "cleanup": cmd_cleanup,
        "loop": lambda: cmd_loop(
            state, engine, monitor, analytics, notifier, args.bankroll
        ),
    }

    try:
        commands[args.command]()
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário")
    except Exception as e:
        logger.error("Erro fatal: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
