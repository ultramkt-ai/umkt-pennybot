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
import signal
import sys
import time
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
from filters import filter_markets
from strategy import generate_signals
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

    total_entries = 0

    # 3. Para cada estratégia ativa
    for strategy_name, strategy in STRATEGIES.items():
        logger.info("--- Estratégia: %s ---", strategy_name)

        # Filtrar elegíveis
        eligible, summaries = filter_markets(cached_markets, strategy, state)
        rejected = [s for s in summaries if not s.passed]
        logger.info(
            "  Filtro: %d elegíveis, %d rejeitados (de %d mercados)",
            len(eligible), len(rejected), len(cached_markets),
        )

        if not eligible:
            continue

        # Gerar sinais (EV > 0, ordenados por EV%)
        slots = strategy.max_positions - state.count_open_positions(strategy_name)
        if slots <= 0:
            logger.info("  Sem slots disponíveis (max=%d)", strategy.max_positions)
            continue

        signals = generate_signals(
            eligible, strategy,
            bankroll=bankroll,
            max_cost_per_position=strategy.max_cost_per_position,
            max_signals=slots,
        )
        logger.info("  Sinais com EV > 0: %d (slots=%d)", len(signals), slots)

        if not signals:
            continue

        # Executar entradas
        results = engine.execute_entries(signals)
        entries = [r for r in results if r.success]
        total_entries += len(entries)

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
  scan       Busca mercados, filtra, gera sinais, executa entradas
  monitor    Um ciclo de monitoramento (preços, TP/SL, resolução)
  report     Relatório consolidado no terminal
  digest     Envia daily digest no Telegram
  export     Exporta trade log (CSV + JSON) e relatório
  status     Status rápido do portfolio
  loop       Ciclo contínuo (scan + monitor + digest)

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
        choices=["scan", "monitor", "report", "digest", "export", "status", "loop"],
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

    args = parser.parse_args()
    setup_logging(args.verbose)

    state, engine, monitor, analytics, notifier = create_components()

    commands = {
        "scan": lambda: cmd_scan(state, engine, notifier, args.bankroll),
        "monitor": lambda: cmd_monitor(monitor, notifier),
        "report": lambda: cmd_report(analytics),
        "digest": lambda: cmd_digest(analytics, notifier),
        "export": lambda: cmd_export(analytics),
        "status": lambda: cmd_status(state),
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
