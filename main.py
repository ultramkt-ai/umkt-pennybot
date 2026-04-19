"""
main.py — Orquestrador principal do Penny-Bot.

Ciclos:
  1. Scanner (1h): varre Gamma API → market_cache
  2. Strategy (contínuo): market_cache → TradeSignals
  3. Paper Engine: executa ordens (SQLite)
  4. Monitor (5min): polling preços → TP/SL/resolução

Threads:
  - Main: scanner + strategy
  - Thread 1: monitor de preços

Modo: paper (default) ou live (via BOT_MODE env var).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone

from config import (
    MODE,
    SCAN_INTERVAL_SECONDS,
    MONITOR_INTERVAL_SECONDS,
    STRATEGIES,
    POLYMARKET_TAGS,
    get_tag_id,
    resolve_allowed_categories,
    classify_market_by_tags,
)
from state import StateManager
from paper_engine import PaperEngine
from strategy import StrategyEngine
from monitor import Monitor
from analytics import Analytics
from gamma_client import fetch_events_by_tag, normalize_market


# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ─── Inicialização ───────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Iniciando Penny-Bot (mode=%s)", MODE)

    state = StateManager()
    engine = PaperEngine(state, mode=MODE)
    analytics = Analytics(state)

    strategy_engines: dict[str, StrategyEngine] = {}
    for name, params in STRATEGIES.items():
        allowed = resolve_allowed_categories(params)
        strategy_engines[name] = StrategyEngine(state, params, allowed)
        logger.info(
            "Strategy %s: max_price=$%.2f, max_positions=%d, allowed=%s",
            name, params.max_price, params.max_positions,
            sorted(allowed) if allowed else "NONE (disabled)",
        )

    monitor = Monitor(state, engine)

    # Thread 1: Monitor de preços (5min)
    monitor_thread = threading.Thread(
        target=_run_monitor_loop,
        args=(monitor,),
        daemon=True,
        name="monitor",
    )
    monitor_thread.start()
    logger.info("Monitor thread started (interval=%ds)", MONITOR_INTERVAL_SECONDS)

    # Main thread: Scanner + Strategy (1h)
    logger.info("Main thread: scanner + strategy (interval=%ds)", SCAN_INTERVAL_SECONDS)

    try:
        while True:
            cycle_start = datetime.now(timezone.utc)

            logger.info("=== SCANNER CYCLE ===")
            scanned_count = run_scanner(state)
            logger.info("Scanner: %d mercados escaneados", scanned_count)

            for name, strat_engine in strategy_engines.items():
                signals = strat_engine.scan_markets()
                if signals:
                    logger.info("Strategy %s: %d sinais gerados", name, len(signals))
                    results = engine.execute_entries(signals)
                    successful = sum(1 for r in results if r.success)
                    logger.info("Strategy %s: %d/%d ordens executadas", name, successful, len(signals))

            stats = state.get_stats_summary()
            logger.info(
                "Portfolio: %d abertas, %d fechadas, PnL=$%.2f, ROI=%.1f%%",
                stats["open_positions"],
                stats["closed_positions"],
                stats["total_pnl"],
                stats["roi"] * 100,
            )

            elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
            sleep_time = max(0, SCAN_INTERVAL_SECONDS - elapsed)
            if sleep_time > 0:
                logger.info("Próximo scan em %d segundos", sleep_time)
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Shutdown solicitado pelo usuário")
    except Exception as e:
        logger.exception("Erro fatal no main loop: %s", e)
        raise


# ─── Scanner ─────────────────────────────────────────────────────────────────

def run_scanner(state: StateManager) -> int:
    """Varre todas as categorias, atualiza market_cache."""
    total = 0
    for category, tag_id in POLYMARKET_TAGS.items():
        try:
            count = scan_category(state, category, tag_id)
            total += count
        except Exception as e:
            logger.error("Erro ao escanear categoria %s (tag=%d): %s", category, tag_id, e)
    return total


def scan_category(state: StateManager, category: str, tag_id: int) -> int:
    """Escaneia uma categoria específica e atualiza market_cache."""
    count = 0
    for event in fetch_events_by_tag(tag_id, closed=False, active=True, liquidity_min=100.0):
        markets = event.get("markets") or []
        for raw_market in markets:
            market = normalize_market(raw_market, parent_event=event)
            market["category"] = classify_market_by_tags(market.get("tags", []))
            if market["category"] != category:
                continue
            state.upsert_market(market)
            count += 1
    return count


# ─── Monitor Loop ────────────────────────────────────────────────────────────

def _run_monitor_loop(monitor: Monitor) -> None:
    """Loop infinito do monitor de preços."""
    while True:
        try:
            result = monitor.run_cycle()
            if result.exits_executed > 0 or result.resolutions_detected > 0:
                logger.info(
                    "Monitor: %d exits, %d resoluções",
                    result.exits_executed, result.resolutions_detected,
                )
        except Exception as e:
            logger.exception("Erro no monitor loop: %s", e)
        time.sleep(MONITOR_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
