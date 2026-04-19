"""
gamma_client.py — Cliente HTTP minimalista para a Gamma API da Polymarket.

Responsabilidades:
  - Fazer requests GET com retry em falhas transientes
  - Respeitar rate limits (/events: 500/10s, /markets: 300/10s)
  - Parsing defensivo de campos que vêm como string JSON-encoded
    (outcomePrices, clobTokenIds, outcomes)

Não classifica, não filtra, não decide — só busca e normaliza.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import requests

from config import GAMMA_API_BASE


# ─── Rate limit simples (token bucket por endpoint) ──────────────────────────
#
# A doc oficial fala em 500/10s para /events. Como vamos fazer 1 request por
# categoria em loop, na prática ficamos muito abaixo — o throttle aqui é só
# uma rede de segurança em caso de bug/retries.

_MIN_INTERVAL_SECONDS = 0.1   # no máximo 10 req/s por endpoint
_last_request_time: dict[str, float] = {}


def _throttle(endpoint: str) -> None:
    """Garante um intervalo mínimo entre chamadas ao mesmo endpoint."""
    now = time.monotonic()
    last = _last_request_time.get(endpoint, 0.0)
    wait = _MIN_INTERVAL_SECONDS - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_time[endpoint] = time.monotonic()


# ─── GET com retry ───────────────────────────────────────────────────────────

class GammaAPIError(Exception):
    """Erro irrecuperável da Gamma API."""


def _get_json(
    endpoint: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    timeout: float = 15.0,
) -> Any:
    """
    GET /<endpoint> com retry exponencial em 429/5xx/timeout.
    Retorna JSON decodificado. Lança GammaAPIError se falhar definitivamente.
    """
    url = f"{GAMMA_API_BASE}/{endpoint.lstrip('/')}"

    for attempt in range(max_retries):
        _throttle(endpoint)
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise GammaAPIError(f"Falha de rede em {url}: {e}") from e
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                raise GammaAPIError(f"Resposta inválida de {url}: {e}") from e

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == max_retries - 1:
                raise GammaAPIError(
                    f"{url} retornou {resp.status_code} após {max_retries} tentativas"
                )
            time.sleep(2 ** attempt)
            continue

        # 4xx não retriáveis
        raise GammaAPIError(f"{url} retornou {resp.status_code}: {resp.text[:200]}")

    raise GammaAPIError(f"Retries esgotados em {url}")


# ─── Parsing defensivo de campos da API ──────────────────────────────────────
#
# A Gamma API retorna alguns campos como STRING contendo JSON, por razões
# históricas. Temos que parsear manualmente.

def _parse_json_string_list(value: Any) -> list:
    """
    Parseia campos como 'outcomePrices' = '["0.65","0.35"]' para [0.65, 0.35].
    Aceita também listas já parseadas, strings vazias e None.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Converte para float tolerando string, None, '', etc."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ─── API pública: buscar eventos e mercados ──────────────────────────────────

def fetch_events_by_tag(
    tag_id: int,
    *,
    closed: bool = False,
    active: bool = True,
    page_size: int = 100,
    max_pages: int = 20,
    liquidity_min: float | None = None,
    order: str = "volume24hr",
    ascending: bool = False,
) -> Iterator[dict]:
    """
    Itera sobre todos os eventos de uma tag, paginando com limit/offset.

    Args:
        tag_id: ID oficial da tag (ver POLYMARKET_TAGS em config.py)
        closed: se True, inclui mercados fechados (padrão: só abertos)
        active: se True, só eventos ativos (padrão: True)
        page_size: itens por página (100 é seguro, API aceita até 500)
        max_pages: teto de páginas para evitar loops infinitos
        liquidity_min: filtra server-side por liquidez mínima (em USD).
                       Transfere menos dados — recomendado se souber o mínimo.
        order: campo para ordenação. Default 'volume24hr' traz os mercados
               mais ativos primeiro.
        ascending: direção da ordenação. Default False = decrescente.

    Yields eventos BRUTOS da API (dicts sem normalizar) — quem usa decide
    o que fazer com os campos. O scanner normaliza depois.

    Rate limit: /events tem 500 req/10s. Com page_size=100 e max_pages=20,
    pior caso = 20 req por categoria, bem dentro do limite.
    """
    offset = 0
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "tag_id": tag_id,
            "closed": str(closed).lower(),
            "active": str(active).lower(),
            "limit": page_size,
            "offset": offset,
            "related_tags": "true",  # inclui sub-tags da categoria (ex: BTC dentro de Crypto)
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if liquidity_min is not None:
            params["liquidity_min"] = liquidity_min

        batch = _get_json("events", params=params)

        if not isinstance(batch, list) or len(batch) == 0:
            return

        for event in batch:
            yield event

        if len(batch) < page_size:
            return  # última página

        offset += page_size


def normalize_market(raw_market: dict, parent_event: dict | None = None) -> dict:
    """
    Transforma um market bruto da API num dict plano com os campos que o bot usa.

    Campos chave extraídos/parseados:
      - market_id, condition_id, event_id, question
      - yes_price, no_price (parseados de outcomePrices string)
      - yes_token_id, no_token_id (parseados de clobTokenIds string)
      - liquidity, volume, end_date, active, closed
      - tags (lista de dicts {id, label, ...}) vinda do parent event, se houver
    """
    prices = _parse_json_string_list(raw_market.get("outcomePrices"))
    token_ids = _parse_json_string_list(raw_market.get("clobTokenIds"))
    outcomes = _parse_json_string_list(raw_market.get("outcomes"))

    # Binary market padrão: outcomes = ["Yes", "No"] → índice 0 = YES
    yes_price = _safe_float(prices[0]) if len(prices) > 0 else 0.0
    no_price = _safe_float(prices[1]) if len(prices) > 1 else 0.0
    yes_token_id = str(token_ids[0]) if len(token_ids) > 0 else ""
    no_token_id = str(token_ids[1]) if len(token_ids) > 1 else ""

    # Tags vêm no evento pai (a Gamma retorna tags no Event, não no Market)
    event_tags: list[dict] = []
    event_id = ""
    if parent_event is not None:
        event_tags = parent_event.get("tags") or []
        event_id = str(parent_event.get("id", ""))

    return {
        "market_id": str(raw_market.get("id", "")),
        "condition_id": str(raw_market.get("conditionId", "")),
        "event_id": event_id,
        "question": raw_market.get("question", ""),
        "slug": raw_market.get("slug", ""),
        "outcomes": outcomes,
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "liquidity": _safe_float(raw_market.get("liquidity")),
        "volume": _safe_float(raw_market.get("volume")),
        "volume_24hr": _safe_float(raw_market.get("volume24hr")),
        "end_date": raw_market.get("endDate", ""),
        "active": bool(raw_market.get("active", False)),
        "closed": bool(raw_market.get("closed", False)),
        "accepting_orders": bool(raw_market.get("acceptingOrders", False)),
        "tags": event_tags,
    }


# ─── Wallet Positions API ────────────────────────────────────────────────────

from dataclasses import dataclass


@dataclass
class WalletPosition:
    """Posição aberta em uma wallet Polymarket."""
    market_id: str
    condition_id: str
    event_id: str
    question: str
    side: str  # "YES" ou "NO"
    shares: float
    avg_price: float
    total_cost: float
    current_value: float
    realized_pnl: float
    unrealized_pnl: float
    token_id: str


def get_wallet_positions(wallet_address: str) -> list[WalletPosition]:
    """
    Busca todas as posições abertas de uma wallet na Polymarket API.

    Endpoint: GET /positions?user={wallet_address}&market_status=active
    Retorna lista de WalletPosition com dados normalizados.

    A API retorna posições com:
      - market, outcome (YES/NO), quantity (shares), averagePrice
      - realizedPnl, totalCost, position (current value)
    """
    params = {
        "user": wallet_address,
        "market_status": "active",
    }

    data = _get_json("positions", params=params)

    if not isinstance(data, list):
        return []

    positions: list[WalletPosition] = []

    for item in data:
        market = item.get("market", {})
        outcome = item.get("outcome", "")
        side = "YES" if outcome == "YES" else "NO"

        # Token ID vem do market outcomes
        outcomes = _parse_json_string_list(market.get("outcomes"))
        token_ids = _parse_json_string_list(market.get("clobTokenIds"))

        # Mapear outcome → token_id
        token_id = ""
        if outcome == "YES" and len(token_ids) > 0:
            token_id = str(token_ids[0])
        elif outcome == "NO" and len(token_ids) > 1:
            token_id = str(token_ids[1])

        shares = _safe_float(item.get("quantity"), 0.0)
        avg_price = _safe_float(item.get("averagePrice"), 0.0)
        total_cost = _safe_float(item.get("totalCost"), 0.0)
        current_value = _safe_float(item.get("position"), 0.0)
        realized_pnl = _safe_float(item.get("realizedPnl"), 0.0)
        unrealized_pnl = current_value - total_cost

        positions.append(WalletPosition(
            market_id=str(market.get("id", "")),
            condition_id=str(market.get("conditionId", "")),
            event_id=str(market.get("event", {}).get("id", "")),
            question=market.get("question", ""),
            side=side,
            shares=shares,
            avg_price=avg_price,
            total_cost=total_cost,
            current_value=current_value,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            token_id=token_id,
        ))

    return positions
