"""
scanner.py — Busca mercados ativos na Gamma API e atualiza o cache.

Estratégia: uma chamada por categoria permitida (via tag_id).
A Polymarket permite filtrar server-side com ?tag_id=X, o que é muito
mais eficiente do que puxar tudo e filtrar depois.

O scanner não toma decisões de trade — só alimenta o state com mercados
normalizados. Os filtros e a strategy engine rodam em cima disso depois.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import (
    ALLOWED_CATEGORIES,
    POLYMARKET_TAGS,
    get_tag_id,
    classify_market_by_tags,
)
from gamma_client import fetch_events_by_tag, normalize_market, GammaAPIError
from state import StateManager


logger = logging.getLogger(__name__)


# ─── Resultado do scan ───────────────────────────────────────────────────────

@dataclass
class ScanResult:
    """Resumo do que o scanner encontrou numa rodada."""

    total_markets: int = 0
    by_category: dict[str, int] = None
    skipped_unknown_tag: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.by_category is None:
            self.by_category = {}
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        by_cat = ", ".join(f"{k}={v}" for k, v in sorted(self.by_category.items()))
        return (
            f"Scan: {self.total_markets} mercados | {by_cat} | "
            f"skipped={self.skipped_unknown_tag} errors={len(self.errors)}"
        )


# ─── Scanner principal ───────────────────────────────────────────────────────

def scan_allowed_categories(
    state: StateManager,
    categories: tuple[str, ...] = ALLOWED_CATEGORIES,
) -> ScanResult:
    """
    Para cada categoria permitida: busca eventos na Gamma API,
    extrai mercados, normaliza, classifica, e faz upsert no cache.

    Retorna ScanResult com contagens e erros.
    """
    result = ScanResult()
    seen_market_ids: set[str] = set()

    for category in categories:
        tag_id = get_tag_id(category)
        if tag_id is None:
            result.errors.append(f"Categoria '{category}' sem tag_id mapeado")
            logger.warning("Pulando categoria desconhecida: %s", category)
            continue

        logger.info("Escaneando categoria '%s' (tag_id=%d)", category, tag_id)

        try:
            count_before = result.total_markets
            for event in fetch_events_by_tag(tag_id, closed=False, active=True):
                result.total_markets += _process_event(
                    event, state, seen_market_ids, result
                )
            count_added = result.total_markets - count_before
            result.by_category[category] = count_added
            logger.info("  → %d mercados de %s", count_added, category)

        except GammaAPIError as e:
            err = f"{category}: {e}"
            result.errors.append(err)
            logger.error("Erro escaneando %s: %s", category, e)

    logger.info(result.summary())
    return result


def _process_event(
    event: dict,
    state: StateManager,
    seen: set[str],
    result: ScanResult,
) -> int:
    """
    Extrai mercados de um evento, normaliza, classifica, salva no cache.
    Retorna quantos mercados novos foram processados.

    Dedup: um mesmo market pode aparecer em múltiplas categorias (ex: um
    evento tagueado "crypto" e "finance"). Contamos só a primeira vez.
    """
    markets = event.get("markets") or []
    added = 0

    for raw_market in markets:
        market = normalize_market(raw_market, parent_event=event)

        if not market["market_id"] or market["market_id"] in seen:
            continue
        seen.add(market["market_id"])

        category = classify_market_by_tags(market["tags"])
        market["category"] = category

        state.upsert_market(market)
        added += 1

    return added


# ─── CLI de debug ────────────────────────────────────────────────────────────
#
# Útil pra rodar sozinho e inspecionar o que a API devolve,
# sem precisar subir o bot inteiro.

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = StateManager()
    logger.info("Categorias permitidas: %s", list(ALLOWED_CATEGORIES))
    logger.info("Tags disponíveis: %s", POLYMARKET_TAGS)

    result = scan_allowed_categories(state)

    print("\n" + "=" * 60)
    print(result.summary())
    print("=" * 60)

    if result.errors:
        print("\nErros:")
        for err in result.errors:
            print(f"  - {err}")

    # Exemplo: mostra 3 mercados do cache
    cached = state.get_active_markets()[:3]
    if cached:
        print(f"\nExemplo de mercados no cache ({len(cached)} de {result.total_markets}):")
        for m in cached:
            print(f"  [{m['category']}] {m['question'][:70]}")
            print(f"    YES={m['yes_price']:.3f}  NO={m['no_price']:.3f}  "
                  f"liq=${m['liquidity']:,.0f}")


if __name__ == "__main__":
    main()
