#!/usr/bin/env python3
"""Debug do scan - verifica filtros."""
import logging
from state import StateManager
from scanner import scan_allowed_categories
from filters import filter_markets
from config import PENNY_STRATEGY, NO_SYSTEMATIC_STRATEGY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

state = StateManager()

# Fazer scan
print("=== INICIANDO SCAN ===")
result = scan_allowed_categories(state)
print(result.summary())

# Verificar cache
cached = state.get_active_markets()
print(f"\nMercados no cache: {len(cached)}")

if cached:
    # Testar filtros no primeiro mercado
    m = cached[0]
    print(f"\nTestando filtros no primeiro mercado:")
    print(f"  Category: {m.get('category')}")
    print(f"  YES price: {m.get('yes_price')}")
    print(f"  NO price: {m.get('no_price')}")
    print(f"  Liquidity: {m.get('liquidity')}")
    print(f"  End date: {m.get('end_date')}")
    
    # Testar filtro de preço para penny
    from filters import filter_price
    result_penny = filter_price(m, PENNY_STRATEGY)
    print(f"\nFiltro preço (penny): {result_penny.passed} - {result_penny.reason}")
    
    result_no = filter_price(m, NO_SYSTEMATIC_STRATEGY)
    print(f"Filtro preço (NO sist): {result_no.passed} - {result_no.reason}")
    
    # Testar filtro de liquidez
    from filters import filter_liquidity
    result_liq = filter_liquidity(m, PENNY_STRATEGY)
    print(f"Filtro liquidez: {result_liq.passed} - {result_liq.reason}")
    
    # Testar filtro de expiração
    from filters import filter_expiry
    result_exp = filter_expiry(m, PENNY_STRATEGY)
    print(f"Filtro expiração: {result_exp.passed} - {result_exp.reason}")
else:
    print("Nenhum mercado no cache!")
