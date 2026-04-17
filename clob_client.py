"""
clob_client.py — Cliente para os endpoints PÚBLICOS do CLOB.

Cobre apenas leitura de preços — o que o monitor precisa. Ordens
(POST /order, DELETE /order) virão quando migrarmos para live, e
nesse momento usaremos o SDK oficial py-clob-client em vez de HTTP direto.

Referências:
  - https://docs.polymarket.com/api-reference/data/get-midpoint-price
  - https://docs.polymarket.com/api-reference/market-data/get-midpoint-prices-request-body
"""

from __future__ import annotations

import time
from typing import Any

import requests

from config import CLOB_API_BASE


_MIN_INTERVAL_SECONDS = 0.05   # CLOB aceita 1500/10s em /midpoint → folga grande
_last_request_time: dict[str, float] = {}


class CLOBAPIError(Exception):
    """Erro irrecuperável do CLOB API."""


def _throttle(endpoint: str) -> None:
    now = time.monotonic()
    last = _last_request_time.get(endpoint, 0.0)
    wait = _MIN_INTERVAL_SECONDS - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_time[endpoint] = time.monotonic()


def _get_json(
    endpoint: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    timeout: float = 10.0,
) -> Any:
    url = f"{CLOB_API_BASE}/{endpoint.lstrip('/')}"

    for attempt in range(max_retries):
        _throttle(endpoint)
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise CLOBAPIError(f"Falha de rede em {url}: {e}") from e
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                raise CLOBAPIError(f"Resposta inválida de {url}: {e}") from e

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == max_retries - 1:
                raise CLOBAPIError(
                    f"{url} retornou {resp.status_code} após {max_retries} tentativas"
                )
            time.sleep(2 ** attempt)
            continue

        raise CLOBAPIError(f"{url} retornou {resp.status_code}: {resp.text[:200]}")

    raise CLOBAPIError(f"Retries esgotados em {url}")


def _post_json(
    endpoint: str,
    payload: Any,
    max_retries: int = 3,
    timeout: float = 10.0,
) -> Any:
    url = f"{CLOB_API_BASE}/{endpoint.lstrip('/')}"

    for attempt in range(max_retries):
        _throttle(endpoint)
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise CLOBAPIError(f"Falha de rede em {url}: {e}") from e
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                raise CLOBAPIError(f"Resposta inválida de {url}: {e}") from e

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt == max_retries - 1:
                raise CLOBAPIError(f"{url} retornou {resp.status_code}")
            time.sleep(2 ** attempt)
            continue

        raise CLOBAPIError(f"{url} retornou {resp.status_code}: {resp.text[:200]}")

    raise CLOBAPIError(f"Retries esgotados em {url}")


# ─── API pública ─────────────────────────────────────────────────────────────

def get_midpoint(token_id: str) -> float:
    """
    Retorna o midpoint (média de best bid e best ask) de 1 token.

    Response oficial: { "mid_price": "0.45" } ← string, precisa converter.
    Rate limit: /midpoint = 1500 req/10s (muito folgado).
    """
    data = _get_json("midpoint", params={"token_id": token_id})
    # Documentação diz "mid_price" mas API real retorna "mid"
    mid = data.get("mid") or data.get("mid_price", "0")
    try:
        return float(mid)
    except (ValueError, TypeError):
        raise CLOBAPIError(f"mid inválido: {mid!r}")


def get_midpoints(token_ids: list[str]) -> dict[str, float]:
    """
    Batch de midpoints. Mais eficiente que N chamadas individuais quando o
    monitor tem muitas posições abertas.

    Rate limit: /midpoints = 500 req/10s.
    Retorna: { token_id: midpoint_float }
    """
    if not token_ids:
        return {}

    # Formato do body documentado: lista de objetos { token_id }
    payload = [{"token_id": tid} for tid in token_ids]
    data = _post_json("midpoints", payload=payload)

    # Resposta é dict { token_id: "0.45" }
    result: dict[str, float] = {}
    if isinstance(data, dict):
        for tid, price in data.items():
            try:
                result[str(tid)] = float(price)
            except (ValueError, TypeError):
                continue
    return result


def get_order_book(token_id: str) -> dict:
    """
    Retorna order book completo do token. Usado em analytics para spread,
    não no hot path do monitor.
    """
    return _get_json("book", params={"token_id": token_id})
