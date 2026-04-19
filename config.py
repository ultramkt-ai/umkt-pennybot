"""
config.py — Parâmetros centrais do Polymarket Probability Bot.

Todas as constantes, thresholds e URLs ficam aqui.
Nenhum outro módulo deve ter valores hardcoded.
"""

import os
from dataclasses import dataclass, field


# ─── APIs ────────────────────────────────────────────────────────────────────

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"

# ─── Telegram ────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# ─── Persistência ────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("BOT_DB_PATH", "data/positions.db")
SNAPSHOTS_DIR = os.environ.get("BOT_SNAPSHOTS_DIR", "data/snapshots")

# ─── Modo de operação ────────────────────────────────────────────────────────

MODE = os.environ.get("BOT_MODE", "paper")  # "paper" | "live"


@dataclass(frozen=True)
class StrategyParams:
    """Parâmetros de uma estratégia. Imutável após criação."""

    name: str
    side: str                    # "YES" ou "NO"
    max_price: float             # Preço máximo para entrada (em dólares, ex: 0.04)
    min_liquidity: float         # Liquidez mínima em USD
    min_days_to_expiry: int      # Dias mínimos até expiração
    max_days_to_expiry: int      # Dias máximos até expiração
    max_positions: int           # Máximo de posições simultâneas
    max_per_event: int           # Máximo de posições no mesmo evento
    kelly_fraction: float        # Fração de Kelly (0.25 = Quarter-Kelly)
    base_win_rate: float         # Win rate histórica estimada
    take_profit: float           # TP em múltiplo do custo (ex: 3.0 = 3x)
    stop_loss: float             # SL em fração do custo (ex: 0.5 = perde 50%)

    # Bounce exit: fração do TP a partir da qual um bounce fecha a posição.
    # None = bounce só alerta (penny — preserva perfil assimétrico).
    # 0.5 = fecha se preço atingir 50% do caminho entre entry e target_exit.
    #
    # Exemplo NO sist.: entry=$0.30, TP=$0.45, bounce_exit=0.5
    #   → fecha se preço ≥ 0.30 + (0.45-0.30)×0.5 = $0.375
    #
    # Exemplo penny: bounce_exit=None → nunca fecha no bounce (deixa correr
    #   até TP ou resolução, porque o payoff assimétrico é o que gera EV).
    bounce_exit_threshold: float | None = None

    # Override de categorias. None = usa ALLOWED_CATEGORIES global.
    # Lista vazia [] = estratégia desabilitada (não opera em nada).
    # Lista com categorias = opera APENAS nessas categorias.
    allowed_categories: tuple[str, ...] | None = None


# ─── Whitelist global de categorias ──────────────────────────────────────────
#
# Controla em quais áreas o bot opera. Edite esta lista para escolher as áreas
# de apostas (ex: remover "politics" se não quiser eleições).
#
# Tupla vazia () = bot desabilitado globalmente (não opera em nada).
# Para operar em TODAS as categorias, use: tuple(POLYMARKET_TAGS.keys())
#
# Categorias disponíveis estão em POLYMARKET_TAGS (mais abaixo). Cada uma
# corresponde a um tag_id oficial usado pela Gamma API.

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "crypto",
    "sports",
    "tech",
    "finance",
)


def resolve_allowed_categories(strategy: "StrategyParams") -> frozenset[str]:
    """
    Resolve quais categorias uma estratégia pode operar.

    Regra:
      - Se strategy.allowed_categories is None  → usa ALLOWED_CATEGORIES global
      - Se strategy.allowed_categories é tupla  → usa o override (mesmo vazio)

    Retorna frozenset para lookup O(1) nos filtros.
    """
    if strategy.allowed_categories is None:
        return frozenset(ALLOWED_CATEGORIES)
    return frozenset(strategy.allowed_categories)


# ─── Estratégias pré-configuradas ────────────────────────────────────────────

PENNY_STRATEGY = StrategyParams(
    name="penny",
    side="YES",
    max_price=0.04,
    min_liquidity=1_000.0,
    min_days_to_expiry=14,
    max_days_to_expiry=200,
    max_positions=100,
    max_per_event=3,
    kelly_fraction=0.25,
    base_win_rate=0.05,
    take_profit=3.0,
    stop_loss=0.5,
    bounce_exit_threshold=None,   # ← só alerta, preserva payoff assimétrico
)

NO_SYSTEMATIC_STRATEGY = StrategyParams(
    name="no_systematic",
    side="NO",
    max_price=0.50,
    min_liquidity=1_000.0,
    min_days_to_expiry=14,
    max_days_to_expiry=200,
    max_positions=100,
    max_per_event=3,
    kelly_fraction=0.25,
    base_win_rate=0.70,
    take_profit=1.5,
    stop_loss=0.5,
    bounce_exit_threshold=0.5,    # ← fecha se lucro ≥ 50% do caminho até TP
)

STRATEGIES = {
    "penny": PENNY_STRATEGY,
    "no_systematic": NO_SYSTEMATIC_STRATEGY,
}

# ─── Scanner ─────────────────────────────────────────────────────────────────

SCAN_INTERVAL_SECONDS = 3600       # 1 hora entre scans
MARKETS_PER_PAGE = 100             # Paginação da Gamma API

# ─── Monitor ─────────────────────────────────────────────────────────────────

MONITOR_INTERVAL_SECONDS = 300     # 5 minutos entre checks de preço
BOUNCE_THRESHOLD = 0.10            # Variação de 10% para alertar bounce

# ─── Wallet Monitor (Copytrading Reconciliation) ───────────────────────────

WALLET_MONITOR_INTERVAL_SECONDS = 60   # 60 segundos entre reconciliações
WALLET_ADDRESS = os.environ.get(
    "PENNY_BOT_WALLET_ADDRESS",
    "0xa445c59c0531d28a13550f29d734b33520530286"  # Penny strategy wallet
)

# Copytrading Espelho (Mirror Trading)
MIRROR_STRICT = os.environ.get("MIRROR_STRICT", "true").lower() == "true"
# True = replica automaticamente (produção)
# False = só alerta, não executa (review mode / teste)

MAX_POSITIONS_PER_WALLET = int(os.environ.get("MAX_POSITIONS_PER_WALLET", "200"))
# Teto de posições copiadas para evitar exposição excessiva

AUTO_COPY_NEW_POSITIONS = MIRROR_STRICT  # legado: agora controlado por MIRROR_STRICT

# ─── Analytics ────────────────────────────────────────────────────────────────

DRAWDOWN_ALERT_THRESHOLD = 0.20   # Alerta se drawdown > 20%

# ─── Categorias → tag_ids oficiais da Polymarket ─────────────────────────────
#
# A Gamma API tem um sistema oficial de tags. Em vez de classificar mercados
# por keywords (que seria uma adivinhação caseira), usamos os tag_ids reais
# que a API retorna em cada mercado e aceita como filtro em /events.
#
# IDs confirmados em:
#   https://github.com/Polymarket/safe-wallet-integration (constants/categories.ts)
#
# Para descobrir novos tag_ids: GET https://gamma-api.polymarket.com/tags
# Para filtrar mercados por tag:  GET /events?tag_id=21&related_tags=true
#
# Nota: "weather" não existe como tag oficial top-level na Polymarket.
# Mercados de clima aparecem sob outras tags (ex: "Current Events"). Se precisar,
# adicione o tag_id correto após inspecionar /tags.

POLYMARKET_TAGS: dict[str, int] = {
    "politics":     2,
    "finance":      120,
    "crypto":       21,
    "sports":       100639,
    "tech":         1401,
    "entertainment": 596,    # "Culture" na nomenclatura oficial
    "geopolitics":  100265,
}

DEFAULT_CATEGORY = "other"   # fallback para mercados sem tag mapeada


def get_tag_id(category: str) -> int | None:
    """Retorna o tag_id oficial de uma categoria, ou None se não mapeada."""
    return POLYMARKET_TAGS.get(category)


def classify_market_by_tags(market_tags: list[dict]) -> str:
    """
    Dado o campo `tags` de um mercado retornado pela Gamma API,
    retorna a categoria correspondente a UMA das nossas tags conhecidas.

    A API retorna tags como lista de objetos: [{"id": 21, "label": "Crypto", ...}, ...]
    Como um mercado pode ter múltiplas tags, escolhemos a primeira que bate
    com POLYMARKET_TAGS. Retorna DEFAULT_CATEGORY se nenhuma bater.
    """
    if not market_tags:
        return DEFAULT_CATEGORY

    known_ids = {tag_id: cat for cat, tag_id in POLYMARKET_TAGS.items()}
    for tag in market_tags:
        tag_id = tag.get("id")
        # A API às vezes retorna id como string, às vezes como int — normalizar
        try:
            tag_id = int(tag_id) if tag_id is not None else None
        except (ValueError, TypeError):
            continue
        if tag_id in known_ids:
            return known_ids[tag_id]

    return DEFAULT_CATEGORY
