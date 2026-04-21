"""
monitor.py — Polling de preços e resolução de mercados.

Responsabilidades:
  1. Buscar preços atuais via CLOB /midpoints (batch)
  2. Atualizar current_price de cada posição aberta
  3. Checar condições de saída (TP, SL)
  4. Checar resolução de mercados via market_cache
  5. Detectar bounces significativos (variação > threshold)
  6. Executar saídas via paper_engine
  7. Emitir eventos para o Telegram

Roda a cada MONITOR_INTERVAL_SECONDS (5 min por padrão).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config import BOUNCE_THRESHOLD
from state import StateManager
from paper_engine import PaperEngine, ExecutionResult
from clob_client import get_midpoints, CLOBAPIError


logger = logging.getLogger(__name__)


# ─── Eventos emitidos pelo monitor (consumidos pelo Telegram) ────────────────

@dataclass
class MonitorEvent:
    """Evento genérico emitido pelo monitor."""

    event_type: str       # "exit", "bounce", "resolution", "error"
    position_id: int
    market_id: str
    question: str
    details: dict = field(default_factory=dict)


@dataclass
class MonitorResult:
    """Resumo de uma rodada de monitoramento."""

    positions_checked: int = 0
    prices_updated: int = 0
    prices_failed: int = 0
    exits_executed: int = 0
    bounces_detected: int = 0
    resolutions_detected: int = 0
    events: list[MonitorEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Monitor: {self.positions_checked} checked, "
            f"{self.prices_updated} prices updated, "
            f"{self.exits_executed} exits, "
            f"{self.bounces_detected} bounces, "
            f"{self.resolutions_detected} resolutions, "
            f"{len(self.errors)} errors"
        )


# ─── Monitor ─────────────────────────────────────────────────────────────────

class Monitor:
    """
    Monitora posições abertas. Cada chamada a run_cycle() faz:
      1. Carrega posições abertas do state
      2. Busca preços em batch via CLOB
      3. Atualiza preços, detecta bounces
      4. Checa TP/SL
      5. Checa resolução
      6. Executa saídas necessárias
    """

    def __init__(
        self,
        state: StateManager,
        engine: PaperEngine,
        bounce_threshold: float = BOUNCE_THRESHOLD,
    ):
        self.state = state
        self.engine = engine
        self.bounce_threshold = bounce_threshold

    def run_cycle(self) -> MonitorResult:
        """
        Executa um ciclo completo de monitoramento. Retorna MonitorResult
        com todos os eventos emitidos (para o Telegram consumir).
        """
        result = MonitorResult()

        # 1. Carregar posições abertas
        open_positions = self.state.get_open_positions()
        result.positions_checked = len(open_positions)

        if not open_positions:
            logger.debug("Nenhuma posição aberta para monitorar")
            return result

        # 2. Buscar preços em batch
        prices = self._fetch_prices(open_positions, result)

        # 3. Processar cada posição
        for pos in open_positions:
            self._process_position(pos, prices, result)

        logger.info(result.summary())
        return result

    def _fetch_prices(
        self,
        positions: list[dict],
        result: MonitorResult,
    ) -> dict[str, float]:
        """
        Busca midpoints em batch para todos os tokens das posições abertas.
        Retorna {token_id: price}. Posições sem token_id são ignoradas.
        """
        token_ids = [
            pos["token_id"]
            for pos in positions
            if pos.get("token_id")
        ]

        if not token_ids:
            logger.warning("Nenhuma posição tem token_id — preços não consultados")
            return {}

        # Dedup (múltiplas posições podem ter o mesmo token)
        unique_tokens = list(set(token_ids))

        try:
            prices = get_midpoints(unique_tokens)
            result.prices_updated = len(prices)
            logger.debug("Preços obtidos para %d/%d tokens", len(prices), len(unique_tokens))
            return prices
        except CLOBAPIError as e:
            err = f"Falha ao buscar preços: {e}"
            result.errors.append(err)
            logger.error(err)
            return {}

    def _process_position(
        self,
        pos: dict,
        prices: dict[str, float],
        result: MonitorResult,
    ) -> None:
        """Processa uma posição: atualiza preço, detecta bounce, checa saída."""
        position_id = pos["id"]
        token_id = pos.get("token_id", "")
        old_price = pos.get("current_price") or pos["entry_price"]

        # 1. Atualizar preço
        new_price = prices.get(token_id)
        if new_price is None:
            # Sem preço — pode ser token sem book ou falha parcial
            if token_id:
                result.prices_failed += 1
            return

        self.state.update_current_price(
            position_id,
            new_price,
            source="monitor.price_update",
            payload={
                "market_id": pos["market_id"],
                "strategy": pos["strategy"],
                "side": pos["side"],
            },
            record_ledger=True,
        )

        # 2. Detectar bounce significativo (pode fechar posição se bounce_profit)
        exited_by_bounce = self._check_bounce(pos, old_price, new_price, result)
        if exited_by_bounce:
            return

        # 3. Checar resolução primeiro (prioridade sobre TP/SL)
        resolved, resolution = self._check_resolution(pos)
        if resolved:
            self._execute_resolution(pos, resolution, new_price, result)
            return

        # 4. Checar TP/SL
        target_exit = pos.get("target_exit") or 0.99
        stop_price = pos.get("stop_price") or 0.01

        exit_reason = self.engine.check_exit_conditions(
            position_id, new_price, target_exit, stop_price,
        )

        if exit_reason:
            self._execute_exit(pos, new_price, exit_reason, result)

    def _check_bounce(
        self,
        pos: dict,
        old_price: float,
        new_price: float,
        result: MonitorResult,
    ) -> bool:
        """
        Detecta variações significativas no preço.

        Se bounce UP e preço atual ≥ entry_price × (1 + bounce_threshold),
        fecha a posição automaticamente com reason "bounce_profit".

        Retorna True se a posição foi fechada (caller deve pular TP/SL check).
        """
        if old_price <= 0:
            return False

        change_pct = abs(new_price - old_price) / old_price

        if change_pct < self.bounce_threshold:
            return False

        direction = "UP" if new_price > old_price else "DOWN"
        result.bounces_detected += 1

        event = MonitorEvent(
            event_type="bounce",
            position_id=pos["id"],
            market_id=pos["market_id"],
            question=pos.get("market_question", "")[:80],
            details={
                "direction": direction,
                "old_price": round(old_price, 4),
                "new_price": round(new_price, 4),
                "change_pct": round(change_pct, 4),
                "side": pos["side"],
                "strategy": pos["strategy"],
            },
        )
        result.events.append(event)
        self.state.record_ledger_event(
            position_id=pos["id"],
            event_type="bounce_alert",
            strategy=pos["strategy"],
            market_id=pos["market_id"],
            event_id=pos.get("event_id"),
            condition_id=pos.get("condition_id"),
            side=pos["side"],
            position_status=pos.get("status"),
            price=new_price,
            shares=pos.get("shares"),
            notional=(new_price * pos.get("shares", 0)) if pos.get("shares") else None,
            reason=direction.lower(),
            source="monitor.bounce",
            payload=event.details,
        )
        logger.info(
            "BOUNCE %s: %s %s %.4f → %.4f (%.1f%%)",
            direction, pos["side"], pos["market_id"],
            old_price, new_price, change_pct * 100,
        )

        # Checar se deve fechar automaticamente (bounce exit)
        # bounce_exit_pct vem da posição (configurado por estratégia na abertura).
        # None = só alerta (penny). Float = fecha se lucro ≥ X% do caminho até TP.
        bounce_exit_pct = pos.get("bounce_exit_pct")
        if bounce_exit_pct is None or direction != "UP":
            return False  # só alerta, não fecha

        entry_price = pos["entry_price"]
        target_exit = pos.get("target_exit") or 0.99

        # Preço mínimo para fechar = entry + (target - entry) × bounce_exit_pct
        # Ex: entry=$0.30, target=$0.45, pct=0.5 → min=$0.375
        bounce_exit_price = entry_price + (target_exit - entry_price) * bounce_exit_pct

        if new_price >= bounce_exit_price and new_price > entry_price:
            logger.info(
                "BOUNCE EXIT: preço $%.4f ≥ bounce_exit $%.4f (%.0f%% do TP)",
                new_price, bounce_exit_price, bounce_exit_pct * 100,
            )
            self._execute_exit(
                pos,
                new_price,
                "bounce_exit",
                result,
                audit_payload={
                    "bounce_exit_price": bounce_exit_price,
                    "old_price": old_price,
                    "new_price": new_price,
                    "change_pct": change_pct,
                },
                source="monitor.bounce_exit",
            )
            return True  # posição fechada

        return False

    def _check_resolution(self, pos: dict) -> tuple[bool, str | None]:
        """
        Verifica se o mercado da posição foi resolvido, consultando o market_cache.
        Retorna (resolved, resolution) onde resolution é "1" ou "0" ou None.
        """
        cached = self.state.get_cached_market(pos["market_id"])
        if cached is None:
            return False, None

        if cached.get("resolved"):
            return True, cached.get("resolution")

        return False, None

    def _execute_resolution(
        self,
        pos: dict,
        resolution: str | None,
        current_price: float,
        result: MonitorResult,
    ) -> None:
        """Fecha posição por resolução do mercado."""
        side = pos["side"]

        # Determinar exit_price e razão baseado na resolução
        if resolution == "1":
            # Mercado resolveu YES
            if side == "YES":
                exit_price = 1.0
                reason = "resolved_win"
            else:
                exit_price = 0.0
                reason = "resolved_loss"
        elif resolution == "0":
            # Mercado resolveu NO
            if side == "NO":
                exit_price = 1.0
                reason = "resolved_win"
            else:
                exit_price = 0.0
                reason = "resolved_loss"
        else:
            # Resolução ambígua (ex: "0.5" para early resolution)
            exit_price = current_price
            reason = "resolved_loss"

        exec_result = self.engine.execute_exit(
            pos["id"],
            exit_price,
            reason,
            source="monitor.resolution",
            audit_payload={
                "resolution": resolution,
                "current_price": current_price,
                "side": side,
                "market_id": pos["market_id"],
                "strategy": pos["strategy"],
            },
        )
        if exec_result.success:
            result.resolutions_detected += 1
            result.exits_executed += 1
            result.events.append(MonitorEvent(
                event_type="resolution",
                position_id=pos["id"],
                market_id=pos["market_id"],
                question=pos.get("market_question", "")[:80],
                details={
                    "resolution": resolution,
                    "side": side,
                    "exit_price": exit_price,
                    "pnl": exec_result.pnl,
                    "reason": reason,
                },
            ))
        else:
            result.errors.append(exec_result.message)

    def _execute_exit(
        self,
        pos: dict,
        exit_price: float,
        reason: str,
        result: MonitorResult,
        audit_payload: dict | None = None,
        source: str = "monitor.exit",
    ) -> None:
        """Executa saída por TP ou SL."""
        exec_result = self.engine.execute_exit(
            pos["id"],
            exit_price,
            reason,
            source=source,
            audit_payload={
                "current_price": exit_price,
                "side": pos["side"],
                "market_id": pos["market_id"],
                "strategy": pos["strategy"],
                **(audit_payload or {}),
            },
        )
        if exec_result.success:
            result.exits_executed += 1
            result.events.append(MonitorEvent(
                event_type="exit",
                position_id=pos["id"],
                market_id=pos["market_id"],
                question=pos.get("market_question", "")[:80],
                details={
                    "side": pos["side"],
                    "strategy": pos["strategy"],
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "pnl": exec_result.pnl,
                    "reason": reason,
                },
            ))
        else:
            result.errors.append(exec_result.message)
