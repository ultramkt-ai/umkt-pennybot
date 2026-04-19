"""
main.py — Orquestrador principal do Penny-Bot.

Ciclos:
  1. Scanner (1h): varre Gamma API → market_cache
  2. Strategy (contínuo): market_cache → TradeSignals
  3. Paper Engine: executa ordens (SQLite)
  4. Monitor (5min): polling preços → TP/SL/resolução
  5. Wallet Monitor (1min): reconcilia copytrading

Threads:
  - Main: scanner + strategy (mesma thread, roda junto)
  - Thread 1: monitor de preços
  - Thread 2: wallet monitor (reconciliação)

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
    WALLET_MONITOR_INTERVAL_SECONDS,
    WALLET_ADDRESS,
    MIRROR_STRICT,
    MAX_POSITIONS_PER_WALLET,
    STRATEGIES,
)
from state import StateManager
from paper_engine import PaperEngine, ExecutionMode
from strategy import StrategyEngine
from monitor import Monitor
from wallet_monitor import WalletMonitor, run_reconciliation_loop
from analytics import Analytics
from gamma_client import fetch_events_by_tag, normalize_market
from config import get_tag_id, resolve_allowed_categories, classify_market_by_tags


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

    # 1. Inicializar state + engine
    state = StateManager()
    engine = PaperEngine(state, mode=MODE)
    analytics = Analytics(state)

    # 2. Strategy engine (por estratégia)
    strategy_engines: dict[str, StrategyEngine] = {}
    for name, params in STRATEGIES.items():
        allowed = resolve_allowed_categories(params)
        strategy_engines[name] = StrategyEngine(state, params, allowed)
        logger.info(
            "Strategy %s: max_price=$%.2f, max_positions=%d, allowed=%s",
            name, params.max_price, params.max_positions,
            sorted(allowed) if allowed else "NONE (disabled)",
        )

    # 3. Monitor de preços
    monitor = Monitor(state, engine)

    # 4. Wallet monitor (copytrading espelho)
    wallet_monitor = WalletMonitor(
        state, engine, WALLET_ADDRESS,
        mirror_strict=MIRROR_STRICT,
        max_positions=MAX_POSITIONS_PER_WALLET,
    )
    logger.info(
        "Copytrading espelho: address=%s, interval=%ds, strict=%s, max_positions=%d",
        WALLET_ADDRESS, WALLET_MONITOR_INTERVAL_SECONDS, MIRROR_STRICT, MAX_POSITIONS_PER_WALLET
    )

    # 5. Inicializar scanners (um por categoria)
    categories = list(get_tag_id(cat) for cat in get_tag_id.__globals__["POLYMARKET_TAGS"])
    logger.info("Scanner: %d categorias (%s)", len(categories), ", ".join(str(c) for c in categories if c))

    # ─── Threads ─────────────────────────────────────────────────────────────

    # Thread 1: Monitor de preços (5min)
    monitor_thread = threading.Thread(
        target=_run_monitor_loop,
        args=(monitor,),
        daemon=True,
        name="monitor",
    )
    monitor_thread.start()
    logger.info("Monitor thread started (interval=%ds)", MONITOR_INTERVAL_SECONDS)

    # Thread 2: Wallet monitor (1min)
    wallet_thread = threading.Thread(
        target=_run_wallet_loop,
        args=(wallet_monitor,),
        daemon=True,
        name="wallet_monitor",
    )
    wallet_thread.start()
    logger.info("Wallet monitor thread started (interval=%ds)", WALLET_MONITOR_INTERVAL_SECONDS)

    # Main thread: Scanner + Strategy (1h)
    logger.info("Main thread: scanner + strategy (interval=%ds)", SCAN_INTERVAL_SECONDS)

    try:
        while True:
            cycle_start = datetime.now(timezone.utc)

            # 1. Scanner: atualizar market_cache
            logger.info("=== SCANNER CYCLE ===")
            scanned_count = run_scanner(state)
            logger.info("Scanner: %d mercados escaneados", scanned_count)

            # 2. Strategy: gerar sinais + executar
            for name, strat_engine in strategy_engines.items():
                signals = strat_engine.scan_markets()
                if signals:
                    logger.info("Strategy %s: %d sinais gerados", name, len(signals))
                    results = engine.execute_entries(signals)
                    successful = sum(1 for r in results if r.success)
                    logger.info("Strategy %s: %d/%d ordens executadas", name, successful, len(signals))

            # 3. Analytics: snapshot rápido
            stats = state.get_stats_summary()
            logger.info(
                "Portfolio: %d abertas, %d fechadas, PnL=$%.2f, ROI=%.1%%",
                stats["open_positions"],
                stats["closed_positions"],
                stats["total_pnl"],
                stats["roi"] * 100,
            )

            # 4. Sleep até próximo ciclo
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
    """
    Varre todas as categorias, atualiza market_cache.
    Retorna total de mercados escaneados.
    """
    total = 0

    for category, tag_id in get_tag_id.__globals__["POLYMARKET_TAGS"].items():
        try:
            count = scan_category(state, category, tag_id)
            total += count
        except Exception as e:
            logger.error("Erro ao escanear categoria %s (tag=%d): %s", category, tag_id, e)

    return total


def scan_category(state: StateManager, category: str, tag_id: int) -> int:
    """
    Escaneia uma categoria específica e atualiza market_cache.
    Retorna número de mercados processados.
    """
    count = 0

    for event in fetch_events_by_tag(
        tag_id,
        closed=False,
        active=True,
        liquidity_min=100.0,  # filtra mercados mortos
    ):
        markets = event.get("markets") or []
        for raw_market in markets:
            market = normalize_market(raw_market, parent_event=event)

            # Classificar por tags oficiais
            market["category"] = classify_market_by_tags(market.get("tags", []))

            # Só interessa se categoria bater
            if market["category"] != category:
                continue

            # Atualizar cache
            state.upsert_market(market)
            count += 1

    return count


# ─── Monitor Loops ───────────────────────────────────────────────────────────

def _run_monitor_loop(monitor: Monitor) -> None:
    """Loop infinito do monitor de preços."""
    while True:
        try:
            result = monitor.run_cycle()
            if result.exits_executed > 0 or result.resolutions_detected > 0:
                logger.info("Monitor executou %d exits, %d resoluções", result.exits_executed, result.resolutions_detected)
        except Exception as e:
            logger.exception("Erro no monitor loop: %s", e)

        time.sleep(MONITOR_INTERVAL_SECONDS)


def _run_wallet_loop(wallet_monitor: WalletMonitor) -> None:
    """Loop infinito do wallet monitor (copytrading espelho)."""
    while True:
        try:
            result = wallet_monitor.run_cycle()
            if result.new_positions_copied > 0:
                logger.info(
                    "Copytrading copiou %d novas posições da wallet origem",
                    result.new_positions_copied
                )
            if result.exits_executed > 0:
                logger.info(
                    "Copytrading reconciliou %d exits da carteira origem",
                    result.exits_executed
                )
            if result.adjustments_made > 0:
                logger.info(
                    "Copytrading ajustou %d posições (wallet mudou shares)",
                    result.adjustments_made
                )
        except Exception as e:
            logger.exception("Erro no copytrading loop: %s", e)

        time.sleep(WALLET_MONITOR_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
