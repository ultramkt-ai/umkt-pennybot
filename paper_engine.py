"""
paper_engine.py — Executa ordens em modo paper (simulado) ou live.

Responsabilidades:
  - Receber TradeSignals da strategy engine
  - Executar no modo correto (paper salva no SQLite, live chamaria a CLOB API)
  - Verificar condições de saída (TP, SL, resolução)
  - Retornar resultados para o Telegram alertar

A troca paper → live é uma linha no config: MODE = "live".
A interface execute_order() é a mesma — só muda o backend.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from enum import Enum

from config import MODE
from state import StateManager
from strategy import TradeSignal


logger = logging.getLogger(__name__)


# ─── Tipos ───────────────────────────────────────────────────────────────────

class ExecutionMode(Enum):
    PAPER = "paper"
    LIVE = "live"


@dataclass
class ExecutionResult:
    """Resultado de uma execução (abertura ou fechamento)."""

    success: bool
    position_id: int | None
    message: str
    signal: TradeSignal | None = None     # preenchido em aberturas
    pnl: float | None = None              # preenchido em fechamentos
    exit_reason: str | None = None        # preenchido em fechamentos


# ─── Engine ──────────────────────────────────────────────────────────────────

class PaperEngine:
    """
    Gerencia execução de ordens. Modo paper salva no SQLite.
    Modo live (futuro) usaria py-clob-client.
    """

    def __init__(self, state: StateManager, mode: str = MODE):
        self.state = state
        self.mode = ExecutionMode(mode)

        if self.mode == ExecutionMode.LIVE:
            logger.warning(
                "Modo LIVE selecionado — CLOB trading não implementado ainda. "
                "Use py-clob-client quando estiver pronto."
            )

    # ─── Entradas ────────────────────────────────────────────────────────

    def execute_entry(self, signal: TradeSignal) -> ExecutionResult:
        """
        Executa entrada de uma posição a partir de um TradeSignal.

        Paper: salva no SQLite imediatamente.
        Live: chamaria CLOB API para colocar ordem limit.
        """
        if self.mode == ExecutionMode.LIVE:
            return self._execute_entry_live(signal)
        return self._execute_entry_paper(signal)

    def _execute_entry_paper(self, signal: TradeSignal) -> ExecutionResult:
        """Paper: registra posição no SQLite como se a ordem tivesse sido preenchida."""
        if not signal.token_id:
            logger.error(
                "ENTRY BLOCKED: signal sem token_id | strategy=%s market=%s side=%s question=%s",
                signal.strategy_name,
                signal.market_id,
                signal.side,
                signal.question[:80],
            )
            return ExecutionResult(
                success=False,
                position_id=None,
                message="Entrada bloqueada: signal sem token_id monitorável.",
                signal=signal,
            )

        try:
            position_id = self.state.open_position(
                market_id=signal.market_id,
                condition_id=signal.condition_id,
                event_id=signal.event_id,
                strategy=signal.strategy_name,
                side=signal.side,
                entry_price=signal.entry_price,
                shares=signal.shares,
                token_id=signal.token_id,
                target_exit=signal.target_exit,
                stop_price=signal.stop_price,
                bounce_exit_pct=signal.bounce_exit_pct,
                category=signal.category,
                market_question=signal.question,
                audit_payload={
                    "signal": asdict(signal),
                    "execution_mode": self.mode.value,
                },
                source="paper_engine.entry",
            )

            logger.info(
                "PAPER ENTRY: %s %s @ $%.4f × %d = $%.2f | TP=$%.4f SL=$%.4f | %s",
                signal.side, signal.market_id, signal.entry_price,
                signal.shares, signal.cost,
                signal.target_exit, signal.stop_price,
                signal.question[:60],
            )

            return ExecutionResult(
                success=True,
                position_id=position_id,
                message=(
                    f"[PAPER] {signal.side} {signal.shares} shares "
                    f"@ ${signal.entry_price:.4f} = ${signal.cost:.2f} | "
                    f"EV={signal.ev_pct:.0%} | TP=${signal.target_exit:.4f} "
                    f"SL=${signal.stop_price:.4f}"
                ),
                signal=signal,
            )

        except Exception as e:
            logger.error("Falha ao abrir posição paper: %s", e)
            return ExecutionResult(
                success=False,
                position_id=None,
                message=f"Erro ao abrir posição: {e}",
                signal=signal,
            )

    def _execute_entry_live(self, signal: TradeSignal) -> ExecutionResult:
        """
        Live: colocaria ordem limit na CLOB API via py-clob-client.
        Não implementado ainda.
        """
        return ExecutionResult(
            success=False,
            position_id=None,
            message=(
                "Modo LIVE não implementado. "
                "Instale py-clob-client e configure PRIVATE_KEY + FUNDER_ADDRESS."
            ),
            signal=signal,
        )

    # ─── Saídas ──────────────────────────────────────────────────────────

    def execute_exit(
        self,
        position_id: int,
        exit_price: float,
        reason: str,
        *,
        source: str = "paper_engine.exit",
        audit_payload: dict | None = None,
    ) -> ExecutionResult:
        """
        Fecha uma posição aberta.

        Paper: atualiza SQLite com exit_price, calcula PnL.
        Live: colocaria ordem de venda na CLOB API.

        Reasons válidos: "take_profit", "stop_loss", "resolved_win",
                         "resolved_loss", "bounce_exit", "manual", "expired"
        """
        if self.mode == ExecutionMode.LIVE:
            return self._execute_exit_live(position_id, exit_price, reason)
        return self._execute_exit_paper(
            position_id,
            exit_price,
            reason,
            source=source,
            audit_payload=audit_payload,
        )

    def _execute_exit_paper(
        self,
        position_id: int,
        exit_price: float,
        reason: str,
        *,
        source: str = "paper_engine.exit",
        audit_payload: dict | None = None,
    ) -> ExecutionResult:
        try:
            result = self.state.close_position(
                position_id,
                exit_price,
                reason,
                audit_payload={
                    "execution_mode": self.mode.value,
                    **(audit_payload or {}),
                },
                source=source,
            )

            logger.info(
                "PAPER EXIT: pos=%d %s @ $%.4f → $%.4f | PnL=$%.4f (%s) | %s",
                position_id, result["side"], result["entry_price"],
                exit_price, result["pnl"], reason,
                result.get("market_question", "")[:60],
            )

            return ExecutionResult(
                success=True,
                position_id=position_id,
                message=(
                    f"[PAPER] EXIT {result['side']} pos={position_id} "
                    f"@ ${exit_price:.4f} | PnL=${result['pnl']:+.4f} | {reason}"
                ),
                pnl=result["pnl"],
                exit_reason=reason,
            )

        except ValueError as e:
            logger.error("Falha ao fechar posição %d: %s", position_id, e)
            return ExecutionResult(
                success=False,
                position_id=position_id,
                message=f"Erro ao fechar posição {position_id}: {e}",
            )

    def _execute_exit_live(
        self,
        position_id: int,
        exit_price: float,
        reason: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            position_id=position_id,
            message="Modo LIVE não implementado para exits.",
        )

    # ─── Checagem de saída (usado pelo monitor) ──────────────────────────

    def check_exit_conditions(
        self,
        position_id: int,
        current_price: float,
        target_exit: float,
        stop_price: float,
        resolved: bool = False,
        resolution: str | None = None,
    ) -> str | None:
        """
        Verifica se uma posição deve ser fechada com base no preço atual.

        Retorna o motivo ou None se deve manter aberta.
        """
        if resolved and resolution is not None:
            return "resolved_win" if resolution == "1" else "resolved_loss"

        if current_price >= target_exit:
            return "take_profit"

        if current_price <= stop_price:
            return "stop_loss"

        return None

    # ─── Batch operations ────────────────────────────────────────────────

    def execute_entries(
        self,
        signals: list[TradeSignal],
    ) -> list[ExecutionResult]:
        """Executa múltiplos sinais de entrada. Retorna lista de resultados."""
        results: list[ExecutionResult] = []
        for signal in signals:
            result = self.execute_entry(signal)
            results.append(result)
        return results

    # ─── Info ────────────────────────────────────────────────────────────

    def get_portfolio_summary(self) -> dict:
        """Resumo do portfolio combinado com modo de operação."""
        stats = self.state.get_stats_summary()
        stats["mode"] = self.mode.value
        return stats
