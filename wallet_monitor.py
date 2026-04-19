"""
wallet_monitor.py — Copytrading Espelho (Mirror Trading).

Responsabilidades:
  1. Poller da carteira origem (Polymarket API)
  2. Replicar EXATAMENTE a carteira alvo:
     - Nova posição na wallet → abre no bot (mesmos shares, mesmo side)
     - Wallet aumentou posição → aumenta no bot (mesma qtd)
     - Wallet reduziu posição → reduz no bot (mesma %)
     - Wallet fechou posição → fecha no bot
  3. Slippage tracking: compara preço de saída da wallet vs bot
  4. Auditoria completa: divergências, delays, falhas de reconciliação

Roda a cada WALLET_MONITOR_INTERVAL_SECONDS (60s por padrão).
Mais frequente que o monitor normal (5min) porque copytrading exige
baixo lag entre wallet e bot.

Modo de operação:
  - MIRROR_STRICT=True: replica tudo automaticamente (default)
  - MIRROR_STRICT=False: só alerta, não executa (review mode)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import (
    WALLET_MONITOR_INTERVAL_SECONDS,
    WALLET_ADDRESS,
    MIRROR_STRICT,
    MAX_POSITIONS_PER_WALLET,
)
from state import StateManager
from paper_engine import PaperEngine, ExecutionResult
from gamma_client import get_wallet_positions, WalletPosition, GammaAPIError


logger = logging.getLogger(__name__)


# ─── Configurações de Copytrading ────────────────────────────────────────────

MIRROR_STRICT = True  # se False, só alerta sem executar
MAX_POSITIONS_PER_WALLET = 200  # teto de posições copiadas


# ─── Eventos de reconciliação ────────────────────────────────────────────────

@dataclass
class CopytradeEvent:
    """Evento emitido pelo wallet monitor."""

    event_type: str  # "mirror_entry", "mirror_exit", "mirror_adjust", "divergence", "slippage_alert"
    position_id: int | None
    market_id: str
    question: str
    details: dict = field(default_factory=dict)
    severity: str = "info"  # "info", "warning", "error"


@dataclass
class MirrorResult:
    """Resumo de um ciclo de reconciliação."""

    wallet_positions_found: int = 0
    new_positions_copied: int = 0
    exits_executed: int = 0
    adjustments_made: int = 0
    divergences_found: int = 0
    slippage_alerts: int = 0
    events: list[CopytradeEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Copytrading: {self.wallet_positions_found} na wallet, "
            f"{self.new_positions_copied} copiadas, "
            f"{self.exits_executed} exits, "
            f"{self.adjustments_made} ajustes, "
            f"{self.divergences_found} divergências, "
            f"{self.slippage_alerts} slippage alerts, "
            f"{len(self.errors)} erros"
        )


# ─── Copytrading Engine ─────────────────────────────────────────────────────

class WalletMonitor:
    """
    Replica exatamente a carteira alvo (mirror trading).

    Fluxo:
      1. Carrega posições abertas da wallet origem (API Polymarket)
      2. Carrega posições abertas do ledger local (SQLite)
      3. Compara por market_id + side
      4. Executa ações para espelhar:
         - Nova na wallet → abre no bot (mesmos shares, side, market)
         - Wallet aumentou → aumenta no bot (delta shares)
         - Wallet reduziu → reduz no bot (vende delta shares)
         - Wallet fechou → fecha no bot (exit completo)
      5. Calcula slippage em exits (wallet exit price vs bot exit price)
    """

    def __init__(
        self,
        state: StateManager,
        engine: PaperEngine,
        wallet_address: str,
        mirror_strict: bool = MIRROR_STRICT,
        max_positions: int = MAX_POSITIONS_PER_WALLET,
    ):
        self.state = state
        self.engine = engine
        self.wallet_address = wallet_address
        self.mirror_strict = mirror_strict
        self.max_positions = max_positions

        # Contadores para auditoria
        self.total_copied = 0
        self.total_exits = 0
        self.total_adjustments = 0

    def run_cycle(self) -> MirrorResult:
        """Executa um ciclo completo de copytrading."""
        result = MirrorResult()

        # 1. Carregar posições da wallet
        try:
            wallet_positions = get_wallet_positions(self.wallet_address)
            result.wallet_positions_found = len(wallet_positions)
            logger.debug(
                "Wallet %s tem %d posições abertas",
                self.wallet_address, len(wallet_positions)
            )
        except GammaAPIError as e:
            err = f"Falha ao consultar wallet {self.wallet_address}: {e}"
            result.errors.append(err)
            logger.error(err)
            return result

        # 2. Carregar posições locais
        local_positions = self.state.get_open_positions()
        local_by_market = {
            (p["market_id"], p["side"]): p
            for p in local_positions
        }

        # Check: não ultrapassar teto de posições
        if len(local_positions) >= self.max_positions:
            err = f"Limite de {self.max_positions} posições atingido — copytrading pausado"
            result.errors.append(err)
            logger.warning(err)
            return result

        # 3. Processar mudanças
        wallet_markets = set((wp.market_id, wp.side) for wp in wallet_positions)
        local_markets = set(local_by_market.keys())

        # 3a. Novas posições na wallet (espelhar entrada)
        new_markets = wallet_markets - local_markets
        for market_key in new_markets:
            wp = next(w for w in wallet_positions if (w.market_id, w.side) == market_key)
            self._mirror_entry(wp, result)

        # 3b. Posições fechadas na wallet (espelhar saída)
        closed_markets = local_markets - wallet_markets
        for market_key in closed_markets:
            local_pos = local_by_market[market_key]
            self._mirror_exit(local_pos, result)

        # 3c. Posições em ambas → checar ajuste de shares
        common_markets = wallet_markets & local_markets
        for market_key in common_markets:
            wp = next(w for w in wallet_positions if (w.market_id, w.side) == market_key)
            local_pos = local_by_market[market_key]
            self._mirror_adjust(wp, local_pos, result)

        logger.info(result.summary())
        return result

    def _mirror_entry(
        self,
        wp: WalletPosition,
        result: MirrorResult,
    ) -> None:
        """
        Wallet abriu posição nova → abre no bot com mesmos parâmetros.

        Usa o avg_price da wallet como entry_price e os mesmos shares.
        Strategy = "mirror_copy" (não usa EV/kelly — é espelho puro).
        """
        if not self.mirror_strict:
            event = CopytradeEvent(
                event_type="mirror_entry",
                position_id=None,
                market_id=wp.market_id,
                question=wp.question or wp.market_id,
                details={
                    "side": wp.side,
                    "shares": wp.shares,
                    "avg_price": wp.avg_price,
                    "total_cost": wp.total_cost,
                    "wallet_address": self.wallet_address,
                    "mirror_strict": False,
                },
                severity="info",
            )
            result.events.append(event)
            result.new_positions_copied += 1
            logger.info(
                "[DRY RUN] Nova posição na wallet: %s %s × %d @ $%.4f",
                wp.side, wp.market_id, wp.shares, wp.avg_price
            )
            return

        # Executar entrada espelho
        try:
            position_id = self.state.open_position(
                market_id=wp.market_id,
                condition_id=wp.condition_id,
                event_id=wp.event_id,
                strategy="mirror_copy",  # estratégia = espelho (não EV-based)
                side=wp.side,
                entry_price=wp.avg_price,
                shares=wp.shares,
                category="mirror",  # categoria = mirror (não usa tags)
                market_question=wp.question,
            )

            # Salvar metadata adicional da wallet no state (para auditoria)
            self._save_wallet_metadata(position_id, wp)

            result.new_positions_copied += 1
            self.total_copied += 1

            event = CopytradeEvent(
                event_type="mirror_entry",
                position_id=position_id,
                market_id=wp.market_id,
                question=wp.question or wp.market_id,
                details={
                    "side": wp.side,
                    "shares": wp.shares,
                    "entry_price": wp.avg_price,
                    "total_cost": wp.total_cost,
                    "wallet_address": self.wallet_address,
                    "copied_at": datetime.now(timezone.utc).isoformat(),
                },
                severity="info",
            )
            result.events.append(event)

            logger.info(
                "MIRROR ENTRY: pos=%d %s %s × %d @ $%.4f = $%.2f",
                position_id, wp.side, wp.market_id, wp.shares,
                wp.avg_price, wp.total_cost
            )

        except Exception as e:
            err = f"Falha ao copiar entrada {wp.market_id}: {e}"
            result.errors.append(err)
            logger.error(err)

    def _mirror_exit(
        self,
        local_pos: dict,
        result: MirrorResult,
    ) -> None:
        """
        Wallet fechou posição → fecha no bot.

        Tenta obter o exit_price real da wallet (se disponível no polling).
        Se não tiver, usa preço de mercado atual e calcula slippage.
        """
        position_id = local_pos["id"]
        market_id = local_pos["market_id"]
        token_id = local_pos.get("token_id")

        if not self.mirror_strict:
            event = CopytradeEvent(
                event_type="mirror_exit",
                position_id=position_id,
                market_id=market_id,
                question=local_pos.get("market_question", "")[:80],
                details={
                    "side": local_pos["side"],
                    "shares": local_pos["shares"],
                    "entry_price": local_pos["entry_price"],
                    "wallet_address": self.wallet_address,
                    "mirror_strict": False,
                },
                severity="info",
            )
            result.events.append(event)
            result.exits_executed += 1
            logger.info(
                "[DRY RUN] Wallet fechou: %s %s (pos=%d)",
                local_pos["side"], market_id, position_id
            )
            return

        # Buscar preço atual para o exit
        from clob_client import get_midpoint

        exit_price = None
        wallet_exit_price = None  # TODO: capturar do polling da wallet

        try:
            if token_id:
                exit_price = get_midpoint(token_id)
            else:
                exit_price = local_pos.get("current_price") or local_pos["entry_price"]
                logger.warning(
                    "Posição %d sem token_id — usando preço %s para exit",
                    position_id, exit_price
                )
        except GammaAPIError as e:
            err = f"Falha ao obter preço para exit da posição {position_id}: {e}"
            result.errors.append(err)
            logger.error(err)
            return

        # Executar exit local
        exec_result = self.engine.execute_exit(
            position_id=position_id,
            exit_price=exit_price,
            reason="wallet_exit",
        )

        if exec_result.success:
            result.exits_executed += 1
            self.total_exits += 1

            # Calcular slippage (se tivermos wallet_exit_price)
            slippage = None
            slippage_pct = None
            if wallet_exit_price is not None and wallet_exit_price > 0:
                slippage = exit_price - wallet_exit_price
                slippage_pct = slippage / wallet_exit_price

                if abs(slippage_pct) > 0.05:  # >5% slippage → alerta
                    result.slippage_alerts += 1
                    event = CopytradeEvent(
                        event_type="slippage_alert",
                        position_id=position_id,
                        market_id=market_id,
                        question=local_pos.get("market_question", "")[:80],
                        details={
                            "wallet_exit_price": wallet_exit_price,
                            "bot_exit_price": exit_price,
                            "slippage": slippage,
                            "slippage_pct": slippage_pct,
                        },
                        severity="warning",
                    )
                    result.events.append(event)
                    logger.warning(
                        "SLIPPAGE ALERT: pos=%d wallet=$%.4f bot=$%.4f (%.1f%%)",
                        position_id, wallet_exit_price, exit_price, slippage_pct * 100
                    )

            event = CopytradeEvent(
                event_type="mirror_exit",
                position_id=position_id,
                market_id=market_id,
                question=local_pos.get("market_question", "")[:80],
                details={
                    "side": local_pos["side"],
                    "strategy": local_pos["strategy"],
                    "entry_price": local_pos["entry_price"],
                    "exit_price": exit_price,
                    "shares": local_pos["shares"],
                    "pnl": exec_result.pnl,
                    "wallet_exit_price": wallet_exit_price,
                    "slippage": slippage,
                    "slippage_pct": slippage_pct,
                    "wallet_address": self.wallet_address,
                },
                severity="info",
            )
            result.events.append(event)

            logger.info(
                "MIRROR EXIT: pos=%d %s @ $%.4f → $%.4f | PnL=$%.4f | slippage=%s",
                position_id, local_pos["side"], local_pos["entry_price"],
                exit_price, exec_result.pnl,
                f"{slippage_pct:.1%}" if slippage_pct is not None else "N/A"
            )
        else:
            result.errors.append(exec_result.message)
            logger.error("Falha ao reconciliar exit da posição %d: %s", position_id, exec_result.message)

    def _mirror_adjust(
        self,
        wp: WalletPosition,
        local_pos: dict,
        result: MirrorResult,
    ) -> None:
        """
        Wallet ajustou posição (aumentou ou reduziu shares) → ajusta no bot.

        Se wallet tem mais shares → compra delta
        Se wallet tem menos shares → vende delta (partial exit)
        """
        wallet_shares = wp.shares
        local_shares = local_pos["shares"]

        tolerance = 0.01  # 1% de tolerância para arredondamentos
        delta_shares = wallet_shares - local_shares
        delta_pct = abs(delta_shares) / local_shares if local_shares > 0 else 0

        if delta_pct <= tolerance:
            return  # dentro da tolerância

        if not self.mirror_strict:
            action = "aumentou" if delta_shares > 0 else "reduziu"
            event = CopytradeEvent(
                event_type="mirror_adjust",
                position_id=local_pos["id"],
                market_id=local_pos["market_id"],
                question=local_pos.get("market_question", "")[:80],
                details={
                    "side": local_pos["side"],
                    "wallet_shares": wallet_shares,
                    "local_shares": local_shares,
                    "delta_shares": delta_shares,
                    "delta_pct": round(delta_pct, 4),
                    "action": f"wallet {action} {abs(delta_shares):.0f} shares",
                    "mirror_strict": False,
                },
                severity="info",
            )
            result.events.append(event)
            result.adjustments_made += 1
            logger.info(
                "[DRY RUN] Ajuste: %s %s — wallet %d → local %d (delta=%+.0f)",
                local_pos["side"], local_pos["market_id"],
                wallet_shares, local_shares, delta_shares
            )
            return

        # Executar ajuste
        position_id = local_pos["id"]

        if delta_shares > 0:
            # Wallet aumentou → compra delta shares
            # Preço atual de mercado
            from clob_client import get_midpoint

            token_id = local_pos.get("token_id")
            try:
                current_price = get_midpoint(token_id) if token_id else local_pos["entry_price"]
            except GammaAPIError:
                current_price = local_pos["entry_price"]

            try:
                # Adicionar shares à posição existente
                # Nota: state.open_position cria nova posição
                # Para ajuste, precisamos de um método específico
                new_position_id = self.state.open_position(
                    market_id=local_pos["market_id"],
                    condition_id=local_pos["condition_id"],
                    event_id=local_pos["event_id"],
                    strategy="mirror_copy",
                    side=local_pos["side"],
                    entry_price=current_price,
                    shares=delta_shares,
                    category="mirror",
                    market_question=local_pos.get("market_question", ""),
                )

                result.adjustments_made += 1
                self.total_adjustments += 1

                event = CopytradeEvent(
                    event_type="mirror_adjust",
                    position_id=position_id,
                    market_id=local_pos["market_id"],
                    question=local_pos.get("market_question", "")[:80],
                    details={
                        "side": local_pos["side"],
                        "wallet_shares": wallet_shares,
                        "local_shares": local_shares,
                        "delta_shares": delta_shares,
                        "adjustment_price": current_price,
                        "adjustment_type": "increase",
                        "new_position_id": new_position_id,
                    },
                    severity="info",
                )
                result.events.append(event)

                logger.info(
                    "MIRROR ADJUST UP: pos=%d %s %s +%.0f shares @ $%.4f (nova pos=%d)",
                    position_id, local_pos["side"], local_pos["market_id"],
                    delta_shares, current_price, new_position_id
                )

            except Exception as e:
                err = f"Falha ao aumentar posição {position_id}: {e}"
                result.errors.append(err)
                logger.error(err)

        else:
            # Wallet reduziu → vende |delta_shares| (partial exit)
            shares_to_sell = abs(delta_shares)
            token_id = local_pos.get("token_id")

            from clob_client import get_midpoint

            try:
                exit_price = get_midpoint(token_id) if token_id else local_pos["entry_price"]
            except APIError:
                exit_price = local_pos["entry_price"]

            # Calcular PnL parcial
            pnl = (exit_price - local_pos["entry_price"]) * shares_to_sell

            try:
                # Atualizar posição local (reduzir shares)
                self.state.adjust_position_shares(position_id, -shares_to_sell, exit_price)

                result.adjustments_made += 1
                self.total_adjustments += 1

                event = CopytradeEvent(
                    event_type="mirror_adjust",
                    position_id=position_id,
                    market_id=local_pos["market_id"],
                    question=local_pos.get("market_question", "")[:80],
                    details={
                        "side": local_pos["side"],
                        "wallet_shares": wallet_shares,
                        "local_shares": local_shares,
                        "delta_shares": -shares_to_sell,
                        "adjustment_price": exit_price,
                        "adjustment_type": "decrease",
                        "partial_pnl": pnl,
                    },
                    severity="info",
                )
                result.events.append(event)

                logger.info(
                    "MIRROR ADJUST DOWN: pos=%d %s %s -%.0f shares @ $%.4f | PnL=$%.4f",
                    position_id, local_pos["side"], local_pos["market_id"],
                    shares_to_sell, exit_price, pnl
                )

            except Exception as e:
                err = f"Falha ao reduzir posição {position_id}: {e}"
                result.errors.append(err)
                logger.error(err)

    def _save_wallet_metadata(
        self,
        position_id: int,
        wp: WalletPosition,
    ) -> None:
        """
        Salva metadata da wallet para auditoria futura.
        (Implementação futura: tabela wallet_copies ou JSON snapshot)
        """
        # TODO: implementar tabela wallet_copies com:
        # - position_id, wallet_address, wallet_shares, wallet_avg_price
        # - copied_at, wallet_exit_price, exit_delay_seconds
        pass


# ─── Helper para polling contínuo ───────────────────────────────────────────

def run_mirror_loop(
    state: StateManager,
    engine: PaperEngine,
    wallet_address: str,
    interval_seconds: int = WALLET_MONITOR_INTERVAL_SECONDS,
    mirror_strict: bool = MIRROR_STRICT,
    max_positions: int = MAX_POSITIONS_PER_WALLET,
) -> None:
    """
    Roda loop infinito de copytrading espelho.

    Uso: thread dedicada no main.py ou processo separado.
    """
    import time

    monitor = WalletMonitor(state, engine, wallet_address, mirror_strict, max_positions)

    logger.info(
        "Iniciando copytrading espelho para %s (intervalo=%ds, strict=%s)",
        wallet_address, interval_seconds, mirror_strict
    )

    while True:
        try:
            result = monitor.run_cycle()

            # Se houve ações, salvar snapshot para auditoria
            if result.new_positions_copied > 0 or result.exits_executed > 0 or result.adjustments_made > 0:
                state.save_snapshot()

        except Exception as e:
            logger.exception("Erro no ciclo de copytrading: %s", e)

        time.sleep(interval_seconds)
