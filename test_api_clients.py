"""Testes dos novos módulos (clob_client + geoblock) sem chamar a API real."""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch, MagicMock
from geoblock import (
    check_geoblock,
    GeoblockResult,
    is_country_blocked_offline,
    is_country_close_only,
    FULLY_BLOCKED_COUNTRIES,
)
from clob_client import get_midpoint, get_midpoints, CLOBAPIError


print("=== Geoblock: listas offline ===")

# Brasil não está bloqueado
assert is_country_blocked_offline("BR") is False
assert is_country_close_only("BR") is False
print("  BR: não bloqueado, não close-only ✓")

# US está
assert is_country_blocked_offline("US") is True
print("  US: bloqueado ✓")

# Close-only (PL)
assert is_country_close_only("PL") is True
assert is_country_blocked_offline("PL") is False  # close-only != fully blocked
print("  PL: close-only, não totalmente bloqueado ✓")

# Case-insensitive
assert is_country_blocked_offline("us") is True
assert is_country_blocked_offline("us") == is_country_blocked_offline("US")
print("  case-insensitive ✓")


print("\n=== Geoblock: parse de resposta mockada ===")

# Resposta do endpoint (formato documentado)
fake_response = MagicMock()
fake_response.json.return_value = {
    "blocked": False,
    "ip": "189.1.2.3",
    "country": "BR",
    "region": "SP",
}
fake_response.raise_for_status.return_value = None

with patch("geoblock.requests.get", return_value=fake_response):
    result = check_geoblock()

assert isinstance(result, GeoblockResult)
assert result.blocked is False
assert result.country == "BR"
assert result.ip == "189.1.2.3"
print(f"  Resposta BR: blocked={result.blocked}, country={result.country} ✓")


print("\n=== CLOB: get_midpoint parseia string → float ===")

# Resposta real documentada: { "mid_price": "0.45" }
fake_response = MagicMock()
fake_response.status_code = 200
fake_response.json.return_value = {"mid_price": "0.45"}

with patch("clob_client.requests.get", return_value=fake_response):
    mid = get_midpoint("0xtoken_id")

assert isinstance(mid, float)
assert mid == 0.45
print(f"  mid_price='0.45' → {mid} (float) ✓")


print("\n=== CLOB: get_midpoint tolera mid_price malformado ===")

fake_response.json.return_value = {"mid_price": "not a number"}

with patch("clob_client.requests.get", return_value=fake_response):
    try:
        get_midpoint("0xtoken_id")
        assert False, "deveria ter lançado CLOBAPIError"
    except CLOBAPIError as e:
        print(f"  mid_price inválido → CLOBAPIError: {e} ✓")


print("\n=== CLOB: get_midpoints batch ===")

fake_response.json.return_value = {
    "0xaaa": "0.25",
    "0xbbb": "0.75",
    "0xccc": "lixo",  # ← deve ser ignorado
}

with patch("clob_client.requests.post", return_value=fake_response):
    mids = get_midpoints(["0xaaa", "0xbbb", "0xccc"])

assert mids == {"0xaaa": 0.25, "0xbbb": 0.75}
print(f"  batch com 1 inválido: {mids} ✓")

# Lista vazia não faz request
mids = get_midpoints([])
assert mids == {}
print("  lista vazia → {} sem request ✓")


print("\n=== Lista de bloqueados: contagem ===")
print(f"  {len(FULLY_BLOCKED_COUNTRIES)} países totalmente bloqueados conhecidos")
assert len(FULLY_BLOCKED_COUNTRIES) >= 30  # pelo menos os da doc oficial


print("\n✅ Todos os testes dos novos módulos passaram!")
