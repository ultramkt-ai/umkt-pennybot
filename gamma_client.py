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
import logging
import time
from typing import Any, Iterator

import requests

from config import GAMMA_API_BASE


logger = logging.getLogger(__name__)


_MIN_INTERVAL_SECONDS = 0.1
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
    url = f"{GAMMA_API_BASE}/{endpoint.lstrip('/')}"

    for attempt in range(max_retries):
        _throttle(endpoint)
        started_at = time.monotonic()
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            elapsed = time.monotonic() - started_at
        except requests.RequestException as e:
            elapsed = time.monotonic() - started_at
            logger.warning(
                "Gamma request exception: endpoint=%s time=%.3fs attempt=%d/%d error=%s params=%s",
                endpoint,
                elapsed,
                attempt + 1,
                max_retries,
                e,
                params,
            )
            if attempt == max_retries - 1:
                raise GammaAPIError(f"Falha de rede em {url}: {e}") from e
            time.sleep(2 ** attempt)
            continue

        if elapsed > 2.0:
            logger.warning(
                "Gamma slow response: endpoint=%s status=%s time=%.3fs attempt=%d/%d params=%s",
                endpoint,
                resp.status_code,
                elapsed,
                attempt + 1,
                max_retries,
                params,
            )

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                logger.warning(
                    "Gamma invalid JSON: endpoint=%s status=%s time=%.3fs params=%s error=%s",
                    endpoint,
                    resp.status_code,
                    elapsed,
                    params,
                    e,
                )
                raise GammaAPIError(f"Resposta inválida de {url}: {e}") from e

        if resp.status_code == 403:
            logger.warning(
                "Gamma access denied: endpoint=%s status=403 time=%.3fs attempt=%d/%d params=%s",
                endpoint,
                elapsed,
                attempt + 1,
                max_retries,
                params,
            )

        if resp.status_code == 429:
            logger.warning(
                "Gamma rate limit: endpoint=%s status=429 time=%.3fs attempt=%d/%d params=%s",
                endpoint,
                elapsed,
                attempt + 1,
                max_retries,
                params,
            )

        if resp.status_code in (500, 502, 503, 504):
            logger.warning(
                "Gamma server error: endpoint=%s status=%s time=%.3fs attempt=%d/%d params=%s",
                endpoint,
                resp.status_code,
                elapsed,
                attempt + 1,
                max_retries,
                params,
            )

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == max_retries - 1:
                raise GammaAPIError(
                    f"{url} retornou {resp.status_code} após {max_retries} tentativas"
                )
            time.sleep(2 ** attempt)
            continue

        raise GammaAPIError(f"{url} retornou {resp.status_code}: {resp.text[:200]}")

    raise GammaAPIError(f"Retries esgotados em {url}")


# ─── Parsing defensivo ───────────────────────────────────────────────────────

def _parse_json_string_list(value: Any) -> list:
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
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ─── API pública: buscar eventos e mercados ──────────────────────────────────

def fetch_event_by_id(event_id: str | int) -> dict:
    """
    Busca um evento único pelo ID na Gamma API.

    Usado para auditoria e backfill de token_ids de posições já abertas,
    inclusive quando a categoria atual não está mais habilitada no scanner.
    """
    return _get_json(f"events/{event_id}")


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

    Yields eventos BRUTOS da API (dicts sem normalizar).
    """
    offset = 0
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "tag_id": tag_id,
            "closed": str(closed).lower(),
            "active": str(active).lower(),
            "limit": page_size,
            "offset": offset,
            "related_tags": "true",
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
            return

        offset += page_size


def normalize_market(raw_market: dict, parent_event: dict | None = None) -> dict:
    """
    Transforma um market bruto da API num dict plano com os campos que o bot usa.
    """
    prices = _parse_json_string_list(raw_market.get("outcomePrices"))
    token_ids = _parse_json_string_list(raw_market.get("clobTokenIds"))
    outcomes = _parse_json_string_list(raw_market.get("outcomes"))

    yes_price = _safe_float(prices[0]) if len(prices) > 0 else 0.0
    no_price = _safe_float(prices[1]) if len(prices) > 1 else 0.0
    yes_token_id = str(token_ids[0]) if len(token_ids) > 0 else ""
    no_token_id = str(token_ids[1]) if len(token_ids) > 1 else ""

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
