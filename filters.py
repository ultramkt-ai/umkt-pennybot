"""
filters.py — Regras puras de elegibilidade para mercados.

Cada filtro é uma função pura:
    (market, strategy, state) → FilterResult(passed, reason)

Sem IA, sem modelo probabilístico — apenas thresholds configuráveis
do StrategyParams. Dado o mesmo input, sempre a mesma decisão.

O scanner busca mercados → os filtros dizem quais são elegíveis →
a strategy engine decide sizing e EV nos elegíveis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from config import StrategyParams, resolve_allowed_categories
from state import StateManager


# ─── Resultado de um filtro ──────────────────────────────────────────────────

@dataclass(frozen=True)
class FilterResult:
    """Resultado de um filtro individual."""

    passed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.passed


PASS = FilterResult(passed=True, reason="")


def _fail(reason: str) -> FilterResult:
    return FilterResult(passed=False, reason=reason)


# ─── Filtros individuais ─────────────────────────────────────────────────────
#
# Cada filtro recebe um mercado normalizado (dict do gamma_client.normalize_market),
# os parâmetros da estratégia, e opcionalmente o state para checks de posição.
# Retorna FilterResult.
#
# Compostos com apply_all() — falha rápida no primeiro que rejeitar.


def filter_active(market: dict, strategy: StrategyParams, **_) -> FilterResult:
    """Mercado precisa estar ativo e aceitando ordens."""
    if not market.get("active", False):
        return _fail("mercado inativo")
    if market.get("closed", False):
        return _fail("mercado fechado")
    return PASS


def filter_price(market: dict, strategy: StrategyParams, **_) -> FilterResult:
    """
    Verifica se o preço do lado da estratégia está dentro do threshold.

    - Penny (side=YES): yes_price ≤ max_price (ex: 0.04)
    - NO sistemático (side=NO): no_price ≤ max_price (ex: 0.50)

    Preço zero significa sem book — rejeita.
    """
    if strategy.side == "YES":
        price = market.get("yes_price", 0.0)
    else:
        price = market.get("no_price", 0.0)

    if price <= 0:
        return _fail(f"preço {strategy.side}={price:.4f} (sem book)")
    if price > strategy.max_price:
        return _fail(
            f"preço {strategy.side}={price:.4f} > max={strategy.max_price:.4f}"
        )
    return PASS


def filter_liquidity(market: dict, strategy: StrategyParams, **_) -> FilterResult:
    """Liquidez mínima em USD."""
    liq = market.get("liquidity", 0.0)
    if liq < strategy.min_liquidity:
        return _fail(f"liquidez=${liq:,.0f} < min=${strategy.min_liquidity:,.0f}")
    return PASS


def filter_expiry(market: dict, strategy: StrategyParams, **_) -> FilterResult:
    """
    Dias até expiração dentro do range [min_days, max_days].

    end_date vem como ISO string do Gamma API (ex: "2025-12-31T23:59:59Z").
    Se não tiver end_date, rejeita (não sabemos quando expira).
    """
    end_date_str = market.get("end_date", "")
    if not end_date_str:
        return _fail("sem data de expiração")

    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return _fail(f"end_date inválido: {end_date_str!r}")

    now = datetime.now(timezone.utc)
    days_left = (end_date - now).total_seconds() / 86400

    if days_left < strategy.min_days_to_expiry:
        return _fail(
            f"expira em {days_left:.0f}d < min={strategy.min_days_to_expiry}d"
        )
    if days_left > strategy.max_days_to_expiry:
        return _fail(
            f"expira em {days_left:.0f}d > max={strategy.max_days_to_expiry}d"
        )
    return PASS


def filter_category(market: dict, strategy: StrategyParams, **_) -> FilterResult:
    """
    Categoria do mercado está na whitelist da estratégia.

    Resolve: override da estratégia → whitelist global.
    """
    allowed = resolve_allowed_categories(strategy)
    if not allowed:
        return _fail("estratégia sem categorias permitidas (desabilitada)")

    category = market.get("category", "other")
    if category not in allowed:
        return _fail(f"categoria '{category}' não permitida (allowed={sorted(allowed)})")
    return PASS


def filter_max_positions(
    market: dict,
    strategy: StrategyParams,
    state: StateManager | None = None,
    **_,
) -> FilterResult:
    """Não exceder o máximo de posições abertas da estratégia."""
    if state is None:
        return PASS  # sem state, não pode verificar — passa

    count = state.count_open_positions(strategy.name)
    if count >= strategy.max_positions:
        return _fail(
            f"{count} posições abertas >= max={strategy.max_positions}"
        )
    return PASS


def filter_max_per_event(
    market: dict,
    strategy: StrategyParams,
    state: StateManager | None = None,
    **_,
) -> FilterResult:
    """Anti-correlação: não exceder max_per_event no mesmo evento."""
    if state is None:
        return PASS

    event_id = market.get("event_id", "")
    if not event_id:
        return PASS  # sem event_id, não pode verificar — passa

    positions = state.get_positions_for_event(event_id, strategy.name)
    if len(positions) >= strategy.max_per_event:
        return _fail(
            f"{len(positions)} posições no evento '{event_id}' "
            f">= max={strategy.max_per_event}"
        )
    return PASS


def filter_no_duplicate(
    market: dict,
    strategy: StrategyParams,
    state: StateManager | None = None,
    **_,
) -> FilterResult:
    """Não abrir posição duplicada no mesmo mercado + estratégia."""
    if state is None:
        return PASS

    open_positions = state.get_open_positions(strategy=strategy.name)
    for pos in open_positions:
        if pos["market_id"] == market.get("market_id"):
            return _fail(
                f"já tem posição aberta no mercado '{market.get('market_id')}'"
            )
    return PASS


# ─── Pipeline de filtros ─────────────────────────────────────────────────────

# Ordem importa: filtros baratos primeiro, filtros com I/O por último.
ALL_FILTERS = [
    filter_active,          # campo booleano — O(1)
    filter_category,        # lookup em frozenset — O(1)
    filter_price,           # comparação numérica — O(1)
    filter_liquidity,       # comparação numérica — O(1)
    filter_expiry,          # parse de data — O(1) mas com string parsing
    filter_no_duplicate,    # scan de posições — O(n) onde n = posições abertas
    filter_max_positions,   # count no SQLite — O(1) com índice
    filter_max_per_event,   # query no SQLite — O(1) com índice
]


@dataclass
class FilterSummary:
    """Resumo de um mercado passando (ou não) pelo pipeline."""

    market_id: str
    question: str
    passed: bool
    failed_at: str      # nome do filtro que rejeitou ("" se passou)
    reason: str         # motivo da rejeição ("" se passou)


def apply_all(
    market: dict,
    strategy: StrategyParams,
    state: StateManager | None = None,
) -> FilterSummary:
    """
    Roda todos os filtros em sequência. Falha rápida no primeiro que rejeitar.

    Retorna FilterSummary com diagnóstico completo — útil para debug e
    para o Telegram mostrar "mercado X rejeitado por Y".
    """
    for filter_fn in ALL_FILTERS:
        result = filter_fn(market, strategy, state=state)
        if not result:
            return FilterSummary(
                market_id=market.get("market_id", "?"),
                question=market.get("question", "?")[:80],
                passed=False,
                failed_at=filter_fn.__name__,
                reason=result.reason,
            )

    return FilterSummary(
        market_id=market.get("market_id", "?"),
        question=market.get("question", "?")[:80],
        passed=True,
        failed_at="",
        reason="",
    )


def filter_markets(
    markets: list[dict],
    strategy: StrategyParams,
    state: StateManager | None = None,
) -> tuple[list[dict], list[FilterSummary]]:
    """
    Filtra uma lista de mercados. Retorna (elegíveis, todos_os_summaries).

    Uso típico:
        eligible, summaries = filter_markets(cached_markets, PENNY_STRATEGY, state)
        rejected = [s for s in summaries if not s.passed]
    """
    eligible: list[dict] = []
    summaries: list[FilterSummary] = []

    for market in markets:
        summary = apply_all(market, strategy, state=state)
        summaries.append(summary)
        if summary.passed:
            eligible.append(market)

    return eligible, summaries
