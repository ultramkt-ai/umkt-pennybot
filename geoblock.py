"""
geoblock.py — Verificação de restrições geográficas da Polymarket.

O endpoint fica em polymarket.com (não nos subdomínios de API).
Não requer autenticação. Retorna se o IP atual pode ou não colocar ordens.

Uso:
    >>> from geoblock import check_geoblock
    >>> result = check_geoblock()
    >>> if result.blocked:
    ...     print(f"Trading bloqueado em {result.country}")

Referência:
    https://docs.polymarket.com/api-reference/geoblock
"""

from __future__ import annotations

from dataclasses import dataclass

import requests


GEOBLOCK_URL = "https://polymarket.com/api/geoblock"


@dataclass(frozen=True)
class GeoblockResult:
    """Resposta do endpoint de geoblock."""

    blocked: bool
    ip: str
    country: str   # ISO 3166-1 alpha-2 (ex: "BR", "US")
    region: str    # código de região/estado


# Países totalmente bloqueados (para checagem offline de referência)
# Fonte: https://docs.polymarket.com/api-reference/geoblock (abril/2026)
FULLY_BLOCKED_COUNTRIES = frozenset({
    "AU", "BE", "BY", "BI", "CF", "CD", "CU", "DE", "ET", "FR",
    "GB", "IR", "IQ", "IT", "KP", "LB", "LY", "MM", "NI", "NL",
    "RU", "SO", "SS", "SD", "SY", "UM", "US", "VE", "YE", "ZW",
})

# Países "close-only" — podem fechar posições existentes mas não abrir novas
CLOSE_ONLY_COUNTRIES = frozenset({"PL", "SG", "TH", "TW"})


def check_geoblock(timeout: float = 10.0) -> GeoblockResult:
    """
    Consulta o endpoint de geoblock da Polymarket.

    Retorna GeoblockResult com a decisão. Lança requests.RequestException
    em caso de falha de rede — o caller decide se quer bloquear ou só logar.
    """
    resp = requests.get(GEOBLOCK_URL, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    return GeoblockResult(
        blocked=bool(data.get("blocked", True)),  # default conservador
        ip=str(data.get("ip", "")),
        country=str(data.get("country", "")),
        region=str(data.get("region", "")),
    )


def is_country_blocked_offline(country_code: str) -> bool:
    """
    Checagem offline usando a lista conhecida. Útil pra testes ou log antes
    da chamada real. NÃO substitui a consulta ao endpoint — a lista pode
    mudar sem aviso.
    """
    return country_code.upper() in FULLY_BLOCKED_COUNTRIES


def is_country_close_only(country_code: str) -> bool:
    """Countries that can only close existing positions, not open new ones."""
    return country_code.upper() in CLOSE_ONLY_COUNTRIES
