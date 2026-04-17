"""
Testes do filters.py — cada filtro unitário + pipeline end-to-end.

Cenários:
  1. Cada filtro individual (passa e falha)
  2. Pipeline completo com mercado elegível (penny)
  3. Pipeline completo com mercado elegível (NO sistemático)
  4. Pipeline com falha em cada estágio
  5. Anti-correlação (max_per_event)
  6. Sem duplicata
  7. filter_markets batch
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone, timedelta
from config import PENNY_STRATEGY, NO_SYSTEMATIC_STRATEGY, StrategyParams
from state import StateManager
from filters import (
    filter_active,
    filter_price,
    filter_liquidity,
    filter_expiry,
    filter_category,
    filter_max_positions,
    filter_max_per_event,
    filter_no_duplicate,
    apply_all,
    filter_markets,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_market(**overrides) -> dict:
    """Cria mercado fake com defaults sensatos (passaria em tudo)."""
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    base = {
        "market_id": "mkt_001",
        "condition_id": "0xcond1",
        "event_id": "evt_001",
        "question": "Will something happen?",
        "yes_price": 0.03,         # ← passa no penny (max=0.04)
        "no_price": 0.97,
        "yes_token_id": "0xyes",
        "no_token_id": "0xno",
        "liquidity": 5000.0,       # ← passa (min=1000)
        "volume": 10000.0,
        "end_date": future,        # ← 30 dias (range 14-200)
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "category": "crypto",      # ← na whitelist padrão
        "tags": [{"id": 21, "label": "Crypto"}],
    }
    base.update(overrides)
    return base


# ─── Bloco 1: filtros individuais ────────────────────────────────────────────

print("=== Bloco 1: filtros individuais ===")

m = _make_market()

# active
assert filter_active(m, PENNY_STRATEGY)
assert not filter_active(_make_market(active=False), PENNY_STRATEGY)
assert not filter_active(_make_market(closed=True), PENNY_STRATEGY)
print("  filter_active ✓")

# price — penny (YES ≤ 0.04)
assert filter_price(m, PENNY_STRATEGY)
assert not filter_price(_make_market(yes_price=0.05), PENNY_STRATEGY)
assert not filter_price(_make_market(yes_price=0.0), PENNY_STRATEGY)  # sem book
print("  filter_price (penny YES) ✓")

# price — NO sistemático (NO ≤ 0.50)
m_no = _make_market(no_price=0.40)
assert filter_price(m_no, NO_SYSTEMATIC_STRATEGY)
assert not filter_price(_make_market(no_price=0.55), NO_SYSTEMATIC_STRATEGY)
print("  filter_price (NO sist.) ✓")

# liquidity
assert filter_liquidity(m, PENNY_STRATEGY)
assert not filter_liquidity(_make_market(liquidity=500), PENNY_STRATEGY)
print("  filter_liquidity ✓")

# expiry — 30 dias futuro
assert filter_expiry(m, PENNY_STRATEGY)
# muito perto (5 dias < min=14)
near = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
assert not filter_expiry(_make_market(end_date=near), PENNY_STRATEGY)
# muito longe (300 dias > max=200)
far = (datetime.now(timezone.utc) + timedelta(days=300)).isoformat()
assert not filter_expiry(_make_market(end_date=far), PENNY_STRATEGY)
# sem data
assert not filter_expiry(_make_market(end_date=""), PENNY_STRATEGY)
# data inválida
assert not filter_expiry(_make_market(end_date="not-a-date"), PENNY_STRATEGY)
print("  filter_expiry ✓")

# category
assert filter_category(m, PENNY_STRATEGY)  # crypto está na whitelist
assert not filter_category(_make_market(category="politics"), PENNY_STRATEGY)
print("  filter_category ✓")

# category com override de estratégia
crypto_only = StrategyParams(
    name="test", side="YES", max_price=0.04, min_liquidity=1000,
    min_days_to_expiry=14, max_days_to_expiry=200, max_positions=100,
    max_per_event=3, kelly_fraction=0.25, base_win_rate=0.05,
    take_profit=3.0, stop_loss=0.5,
    allowed_categories=("crypto",),
)
assert filter_category(_make_market(category="crypto"), crypto_only)
assert not filter_category(_make_market(category="sports"), crypto_only)
print("  filter_category (com override) ✓")


# ─── Bloco 2: filtros com state ──────────────────────────────────────────────

print("\n=== Bloco 2: filtros com state ===")

state = StateManager(db_path="/tmp/test_filters.db", snapshots_dir="/tmp/test_filters_snap")

# max_positions — sem posições → passa
assert filter_max_positions(m, PENNY_STRATEGY, state=state)
print("  filter_max_positions (sem posições) ✓")

# Abrir uma posição e testar no_duplicate
state.open_position(
    market_id="mkt_001", condition_id="0xcond1", event_id="evt_001",
    strategy="penny", side="YES", entry_price=0.03, shares=100,
    category="crypto",
)
assert not filter_no_duplicate(m, PENNY_STRATEGY, state=state)
print("  filter_no_duplicate (já tem posição) ✓")

# Mercado diferente → passa
m2 = _make_market(market_id="mkt_002")
assert filter_no_duplicate(m2, PENNY_STRATEGY, state=state)
print("  filter_no_duplicate (mercado diferente) ✓")

# max_per_event — já tem 1, adicionar mais 2
state.open_position(
    market_id="mkt_002", condition_id="0xcond2", event_id="evt_001",
    strategy="penny", side="YES", entry_price=0.02, shares=100,
)
state.open_position(
    market_id="mkt_003", condition_id="0xcond3", event_id="evt_001",
    strategy="penny", side="YES", entry_price=0.01, shares=100,
)
# Agora temos 3 no evento → max_per_event=3 → bloqueado
m4 = _make_market(market_id="mkt_004", event_id="evt_001")
assert not filter_max_per_event(m4, PENNY_STRATEGY, state=state)
print("  filter_max_per_event (3/3 → bloqueado) ✓")

# Evento diferente → passa
m5 = _make_market(market_id="mkt_005", event_id="evt_002")
assert filter_max_per_event(m5, PENNY_STRATEGY, state=state)
print("  filter_max_per_event (evento diferente) ✓")


# ─── Bloco 3: pipeline completo ─────────────────────────────────────────────

print("\n=== Bloco 3: pipeline completo ===")

# Mercado perfeito para penny (novo evento, tudo OK)
perfect = _make_market(market_id="mkt_perfect", event_id="evt_new")
summary = apply_all(perfect, PENNY_STRATEGY, state=state)
assert summary.passed
assert summary.failed_at == ""
print(f"  mercado perfeito: passed={summary.passed} ✓")

# Mercado que falha no preço
expensive = _make_market(market_id="mkt_exp", event_id="evt_new", yes_price=0.10)
summary = apply_all(expensive, PENNY_STRATEGY, state=state)
assert not summary.passed
assert summary.failed_at == "filter_price"
print(f"  preço alto: failed_at={summary.failed_at}, reason='{summary.reason}' ✓")

# Mercado NO sistemático que passa
no_market = _make_market(
    market_id="mkt_no1", event_id="evt_no",
    no_price=0.30, category="finance",
)
summary = apply_all(no_market, NO_SYSTEMATIC_STRATEGY, state=state)
assert summary.passed
print(f"  NO sistemático elegível: passed={summary.passed} ✓")


# ─── Bloco 4: filter_markets batch ──────────────────────────────────────────

print("\n=== Bloco 4: filter_markets batch ===")

markets = [
    _make_market(market_id="ok_1", event_id="evt_batch"),
    _make_market(market_id="ok_2", event_id="evt_batch", yes_price=0.02),
    _make_market(market_id="bad_price", event_id="evt_batch", yes_price=0.50),
    _make_market(market_id="bad_liq", event_id="evt_batch", liquidity=100),
    _make_market(market_id="bad_cat", event_id="evt_batch", category="politics"),
]

eligible, summaries = filter_markets(markets, PENNY_STRATEGY, state=state)

passed = [s for s in summaries if s.passed]
failed = [s for s in summaries if not s.passed]
print(f"  {len(eligible)} elegíveis, {len(failed)} rejeitados")
assert len(eligible) == 2  # ok_1 e ok_2
assert len(failed) == 3

# Mostra motivos
for s in failed:
    print(f"    {s.market_id}: {s.failed_at} → {s.reason}")

assert any(s.failed_at == "filter_price" for s in failed)
assert any(s.failed_at == "filter_liquidity" for s in failed)
assert any(s.failed_at == "filter_category" for s in failed)
print("  motivos corretos ✓")


# Cleanup
os.remove("/tmp/test_filters.db")

print("\n✅ Todos os testes de filtros passaram!")
