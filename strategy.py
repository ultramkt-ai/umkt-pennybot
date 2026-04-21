"""
strategy.py — Engine de decisão: EV, sizing (Kelly), ranking.

Recebe mercados que já passaram nos filtros e decide:
  1. Qual o EV esperado de cada posição
  2. Qual o tamanho da posição (Quarter-Kelly)
  3. Quais entrar (ordenados por EV, respeitando limites)

Tudo paramétrico. Zero IA. Dado o mesmo input → mesma decisão.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import StrategyParams


# ─── Resultado da análise de um mercado ──────────────────────────────────────

@dataclass
class TradeSignal:
    """Sinal de entrada gerado pela strategy engine."""

    market_id: str
    condition_id: str
    event_id: str
    question: str
    side: str               # "YES" ou "NO"
    token_id: str           # CLOB token ID para consultar preço no monitor
    entry_price: float      # Preço de entrada (YES ou NO, dependendo da strategy)
    ev: float               # Expected value por share
    ev_pct: float           # EV como % do custo
    shares: int             # Quantidade de shares (inteiro, arredondado para baixo)
    cost: float             # Custo total da posição = entry_price × shares
    target_exit: float      # Preço-alvo de saída (take profit)
    stop_price: float       # Preço de stop loss
    bounce_exit_pct: float | None  # Fração do TP para bounce exit (None = penny)
    category: str
    strategy_name: str
    bankroll: float
    base_win_rate: float
    kelly_fraction_used: float
    max_cost_per_position: float | None = None


# ─── Cálculos ────────────────────────────────────────────────────────────────

def calculate_ev(
    entry_price: float,
    win_rate: float,
    payoff: float = 1.0,
) -> float:
    """
    EV = (win_rate × payoff_líquido) - (loss_rate × custo)

    Para mercados binários da Polymarket:
      - Se YES a $0.03 e resolve YES → ganha $1.00 - $0.03 = $0.97 por share
      - Se resolve NO → perde $0.03 por share

    EV > 0 → posição com expectativa positiva.
    """
    if entry_price <= 0 or entry_price >= payoff:
        return 0.0

    win_payoff = payoff - entry_price
    loss_cost = entry_price

    return (win_rate * win_payoff) - ((1 - win_rate) * loss_cost)


def calculate_kelly_fraction(
    entry_price: float,
    win_rate: float,
    kelly_fraction: float = 0.25,
    payoff: float = 1.0,
) -> float:
    """
    Fração ótima do bankroll segundo Kelly, ajustada por kelly_fraction.

    Quarter-Kelly (kelly_fraction=0.25) é mais conservador e resiste
    melhor a erros na estimativa de win_rate.

    Retorna fração do bankroll [0.0, 1.0]. Zero se EV negativo.
    """
    if entry_price <= 0 or entry_price >= payoff:
        return 0.0

    b = (payoff - entry_price) / entry_price
    p = win_rate
    q = 1.0 - p

    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return 0.0

    return min(full_kelly * kelly_fraction, 1.0)


def calculate_position_size(
    bankroll: float,
    entry_price: float,
    kelly_frac: float,
    max_cost: float | None = None,
) -> int:
    """
    Calcula número de shares para comprar.

    shares = floor(bankroll × kelly_frac / entry_price)

    Arredonda para baixo — não dá para comprar meia share.
    Aplica max_cost se especificado (teto de exposição por posição).
    """
    if entry_price <= 0 or kelly_frac <= 0:
        return 0

    cost_budget = bankroll * kelly_frac
    if max_cost is not None:
        cost_budget = min(cost_budget, max_cost)

    shares = int(cost_budget / entry_price)
    return max(shares, 0)


def calculate_targets(
    entry_price: float,
    strategy: StrategyParams,
) -> tuple[float, float]:
    """
    Calcula preço de take profit e stop loss.

    Penny (side=YES, entry=0.03, TP=3.0, SL=0.5):
      target_exit = 0.03 × 3.0 = 0.09
      stop_price  = 0.03 × (1 - 0.5) = 0.015

    NO sist. (side=NO, entry=0.30, TP=1.5, SL=0.5):
      target_exit = 0.30 × 1.5 = 0.45
      stop_price  = 0.30 × 0.5 = 0.15

    Retorna (target_exit, stop_price).
    """
    target_exit = min(entry_price * strategy.take_profit, 0.99)
    stop_price = max(entry_price * (1 - strategy.stop_loss), 0.0)
    return target_exit, stop_price


# ─── Engine principal ────────────────────────────────────────────────────────

def evaluate_market(
    market: dict,
    strategy: StrategyParams,
    bankroll: float = 1000.0,
    max_cost_per_position: float | None = None,
) -> TradeSignal | None:
    """
    Avalia um mercado elegível e gera um TradeSignal se EV > 0.

    Retorna None se EV ≤ 0 ou sizing = 0 shares.
    """
    if strategy.side == "YES":
        entry_price = market.get("yes_price", 0.0)
        token_id = market.get("yes_token_id", "")
    else:
        entry_price = market.get("no_price", 0.0)
        token_id = market.get("no_token_id", "")

    if entry_price <= 0:
        return None

    # 1. EV
    ev = calculate_ev(entry_price, strategy.base_win_rate)
    if ev <= 0:
        return None

    ev_pct = ev / entry_price

    # 2. Kelly sizing
    kelly_frac = calculate_kelly_fraction(
        entry_price, strategy.base_win_rate, strategy.kelly_fraction
    )
    shares = calculate_position_size(
        bankroll, entry_price, kelly_frac, max_cost_per_position
    )
    if shares <= 0:
        return None

    # 3. Targets
    target_exit, stop_price = calculate_targets(entry_price, strategy)

    return TradeSignal(
        market_id=market.get("market_id", ""),
        condition_id=market.get("condition_id", ""),
        event_id=market.get("event_id", ""),
        question=market.get("question", "")[:100],
        side=strategy.side,
        token_id=token_id,
        entry_price=entry_price,
        ev=round(ev, 6),
        ev_pct=round(ev_pct, 4),
        shares=shares,
        cost=round(entry_price * shares, 4),
        target_exit=round(target_exit, 4),
        stop_price=round(stop_price, 4),
        bounce_exit_pct=strategy.bounce_exit_threshold,
        category=market.get("category", "other"),
        strategy_name=strategy.name,
        bankroll=round(bankroll, 4),
        base_win_rate=round(strategy.base_win_rate, 6),
        kelly_fraction_used=round(kelly_frac, 6),
        max_cost_per_position=max_cost_per_position,
    )


def rank_signals(signals: list[TradeSignal]) -> list[TradeSignal]:
    """Ordena sinais por EV% decrescente."""
    return sorted(signals, key=lambda s: s.ev_pct, reverse=True)


def generate_signals(
    eligible_markets: list[dict],
    strategy: StrategyParams,
    bankroll: float = 1000.0,
    max_cost_per_position: float | None = None,
    max_signals: int | None = None,
) -> list[TradeSignal]:
    """
    Pipeline completo: avalia todos os mercados elegíveis, filtra EV > 0,
    ordena por EV% decrescente, e retorna os top-N sinais.
    """
    signals: list[TradeSignal] = []

    for market in eligible_markets:
        signal = evaluate_market(
            market, strategy, bankroll, max_cost_per_position
        )
        if signal is not None:
            signals.append(signal)

    ranked = rank_signals(signals)

    if max_signals is not None:
        return ranked[:max_signals]
    return ranked
