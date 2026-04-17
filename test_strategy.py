"""
Testes do strategy.py — EV, Kelly, sizing, targets, pipeline.

Todos os cálculos são verificáveis na mão. Cada assert tem o cálculo
esperado como comentário para que alguém possa auditar sem rodar código.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from config import PENNY_STRATEGY, NO_SYSTEMATIC_STRATEGY
from strategy import (
    calculate_ev,
    calculate_kelly_fraction,
    calculate_position_size,
    calculate_targets,
    evaluate_market,
    rank_signals,
    generate_signals,
    TradeSignal,
)


# ─── Bloco 1: calculate_ev ──────────────────────────────────────────────────

print("=== Bloco 1: EV ===")

# Penny: YES a $0.03, win_rate=5%
# EV = 0.05 × (1.00 - 0.03) - 0.95 × 0.03
#    = 0.05 × 0.97 - 0.95 × 0.03
#    = 0.0485 - 0.0285
#    = 0.02
ev = calculate_ev(0.03, 0.05)
assert abs(ev - 0.02) < 1e-9, f"EV penny deveria ser 0.02, got {ev}"
print(f"  Penny YES@$0.03 win=5%: EV=${ev:.4f} (esperado 0.02) ✓")

# NO sistemático: NO a $0.30, win_rate=70%
# entry_price = 0.30 (paga pelo NO)
# EV = 0.70 × (1.00 - 0.30) - 0.30 × 0.30
#    = 0.70 × 0.70 - 0.30 × 0.30
#    = 0.49 - 0.09
#    = 0.40
ev = calculate_ev(0.30, 0.70)
assert abs(ev - 0.40) < 1e-9, f"EV NO deveria ser 0.40, got {ev}"
print(f"  NO sist. NO@$0.30 win=70%: EV=${ev:.4f} (esperado 0.40) ✓")

# EV negativo: YES a $0.50, win_rate=30%
# EV = 0.30 × 0.50 - 0.70 × 0.50 = 0.15 - 0.35 = -0.20
ev = calculate_ev(0.50, 0.30)
assert abs(ev - (-0.20)) < 1e-9
print(f"  EV negativo: YES@$0.50 win=30%: EV=${ev:.4f} (esperado -0.20) ✓")

# Edge cases
assert calculate_ev(0.0, 0.5) == 0.0   # preço zero
assert calculate_ev(1.0, 0.5) == 0.0   # preço = payoff
assert calculate_ev(-0.1, 0.5) == 0.0  # preço negativo
print("  Edge cases (preço 0, 1.0, negativo) → EV=0 ✓")


# ─── Bloco 2: calculate_kelly_fraction ───────────────────────────────────────

print("\n=== Bloco 2: Kelly ===")

# Penny: p=0.05, entry=0.03
# b = (1.00 - 0.03) / 0.03 = 32.33...
# Kelly = (0.05 × 32.33 - 0.95) / 32.33 = (1.617 - 0.95) / 32.33 = 0.0206...
# Quarter-Kelly = 0.0206 × 0.25 = 0.00516...
kf = calculate_kelly_fraction(0.03, 0.05, 0.25)
expected_b = 0.97 / 0.03
expected_full = (0.05 * expected_b - 0.95) / expected_b
expected_quarter = expected_full * 0.25
assert abs(kf - expected_quarter) < 1e-9
print(f"  Penny: kelly_frac={kf:.6f} (esperado {expected_quarter:.6f}) ✓")

# EV negativo → Kelly = 0
kf = calculate_kelly_fraction(0.50, 0.30, 0.25)
assert kf == 0.0
print(f"  EV negativo: kelly_frac={kf} (esperado 0.0) ✓")

# Full Kelly (fraction=1.0) para comparar
kf_full = calculate_kelly_fraction(0.03, 0.05, 1.0)
kf_quarter = calculate_kelly_fraction(0.03, 0.05, 0.25)
assert abs(kf_full - kf_quarter * 4) < 1e-9
print(f"  Full Kelly={kf_full:.6f} = 4 × Quarter={kf_quarter:.6f} ✓")


# ─── Bloco 3: calculate_position_size ────────────────────────────────────────

print("\n=== Bloco 3: Sizing ===")

# Bankroll $1000, entry $0.03, kelly_frac = 0.005
# Budget = 1000 × 0.005 = $5.00
# Shares = floor(5.00 / 0.03) = floor(166.66) = 166
shares = calculate_position_size(1000, 0.03, 0.005)
assert shares == 166
print(f"  $1000 × 0.5% / $0.03 = {shares} shares (esperado 166) ✓")

# Com max_cost
# Budget = min(1000 × 0.005, 2.00) = $2.00
# Shares = floor(2.00 / 0.03) = 66
shares = calculate_position_size(1000, 0.03, 0.005, max_cost=2.00)
assert shares == 66
print(f"  com max_cost=$2: {shares} shares (esperado 66) ✓")

# Kelly zero → 0 shares
assert calculate_position_size(1000, 0.03, 0.0) == 0
# Preço zero → 0 shares
assert calculate_position_size(1000, 0.0, 0.01) == 0
print("  edge cases → 0 shares ✓")


# ─── Bloco 4: calculate_targets ─────────────────────────────────────────────

print("\n=== Bloco 4: Targets ===")

# Penny: entry=0.03, TP=3.0×, SL=0.5
# target = 0.03 × 3.0 = 0.09
# stop = 0.03 × (1-0.5) = 0.015
tp, sl = calculate_targets(0.03, PENNY_STRATEGY)
assert abs(tp - 0.09) < 1e-9
assert abs(sl - 0.015) < 1e-9
print(f"  Penny: TP=${tp} SL=${sl} ✓")

# NO sist: entry=0.30, TP=1.5×, SL=0.5
# target = 0.30 × 1.5 = 0.45
# stop = 0.30 × 0.5 = 0.15
tp, sl = calculate_targets(0.30, NO_SYSTEMATIC_STRATEGY)
assert abs(tp - 0.45) < 1e-9
assert abs(sl - 0.15) < 1e-9
print(f"  NO sist: TP=${tp} SL=${sl} ✓")

# Cap em 0.99 se TP ficaria > 1
tp, sl = calculate_targets(0.50, PENNY_STRATEGY)  # 0.50 × 3.0 = 1.50 → capped 0.99
assert tp == 0.99
print(f"  TP capped: ${tp} (não pode > 0.99) ✓")


# ─── Bloco 5: evaluate_market ───────────────────────────────────────────────

print("\n=== Bloco 5: evaluate_market ===")

market = {
    "market_id": "mkt_001",
    "condition_id": "0xcond1",
    "event_id": "evt_001",
    "question": "Will BTC hit $100k?",
    "yes_price": 0.03,
    "no_price": 0.97,
    "category": "crypto",
}

signal = evaluate_market(market, PENNY_STRATEGY, bankroll=1000)
assert signal is not None
assert signal.side == "YES"
assert signal.entry_price == 0.03
assert signal.ev > 0
assert signal.shares > 0
assert signal.cost == round(0.03 * signal.shares, 4)
assert signal.target_exit == 0.09
assert signal.stop_price == 0.015
print(f"  Signal: {signal.shares} shares @ ${signal.entry_price}")
print(f"    EV=${signal.ev} ({signal.ev_pct:.1%}), cost=${signal.cost}")
print(f"    TP=${signal.target_exit}, SL=${signal.stop_price} ✓")

# NO sistemático
no_market = {
    "market_id": "mkt_no1",
    "condition_id": "0xno1",
    "event_id": "evt_no",
    "question": "Will it rain tomorrow?",
    "yes_price": 0.70,
    "no_price": 0.30,
    "category": "finance",
}
signal_no = evaluate_market(no_market, NO_SYSTEMATIC_STRATEGY, bankroll=1000)
assert signal_no is not None
assert signal_no.side == "NO"
assert signal_no.entry_price == 0.30
assert signal_no.ev > 0
print(f"  NO signal: {signal_no.shares} shares @ ${signal_no.entry_price}")
print(f"    EV=${signal_no.ev} ({signal_no.ev_pct:.1%}) ✓")

# EV negativo → None
bad_market = {
    "market_id": "bad",
    "yes_price": 0.50,  # 50¢ é caro demais para penny com 5% win rate
    "no_price": 0.50,
}
assert evaluate_market(bad_market, PENNY_STRATEGY) is None
print("  EV negativo → None ✓")


# ─── Bloco 6: ranking e pipeline ────────────────────────────────────────────

print("\n=== Bloco 6: pipeline generate_signals ===")

markets = [
    {"market_id": "a", "condition_id": "ca", "event_id": "ea",
     "question": "A?", "yes_price": 0.04, "no_price": 0.96, "category": "crypto"},
    {"market_id": "b", "condition_id": "cb", "event_id": "eb",
     "question": "B?", "yes_price": 0.02, "no_price": 0.98, "category": "tech"},
    {"market_id": "c", "condition_id": "cc", "event_id": "ec",
     "question": "C?", "yes_price": 0.03, "no_price": 0.97, "category": "sports"},
    {"market_id": "d", "condition_id": "cd", "event_id": "ed",
     "question": "D?", "yes_price": 0.80, "no_price": 0.20, "category": "crypto"},  # EV neg
]

signals = generate_signals(markets, PENNY_STRATEGY, bankroll=1000)

# D deve ser filtrado (EV negativo a $0.80 com 5% win rate)
assert len(signals) == 3
print(f"  {len(signals)} sinais gerados (4 markets - 1 EV neg) ✓")

# Devem estar ordenados por EV% decrescente
# $0.02 tem EV%=(0.03/0.02)=1.5 mais alto que $0.03 e $0.04
assert signals[0].market_id == "b"  # $0.02 tem melhor EV%
print(f"  Ranking: {[s.market_id for s in signals]} (b=melhor EV%) ✓")

# EV% decresce
for i in range(len(signals) - 1):
    assert signals[i].ev_pct >= signals[i+1].ev_pct
print("  EV% estritamente decrescente ✓")

# max_signals limita
signals_top2 = generate_signals(markets, PENNY_STRATEGY, max_signals=2)
assert len(signals_top2) == 2
assert signals_top2[0].market_id == "b"
print(f"  max_signals=2: {[s.market_id for s in signals_top2]} ✓")


# ─── Bloco 7: verify math by hand ───────────────────────────────────────────

print("\n=== Bloco 7: verificação manual ===")

# Para o market "b" (YES@$0.02, win=5%):
# EV = 0.05 × 0.98 - 0.95 × 0.02 = 0.049 - 0.019 = 0.03
# EV% = 0.03 / 0.02 = 1.50 (150%)
sig_b = signals[0]
assert abs(sig_b.ev - 0.03) < 1e-6
assert abs(sig_b.ev_pct - 1.50) < 1e-4
print(f"  Market B: EV={sig_b.ev}, EV%={sig_b.ev_pct} ✓")

# Kelly: b = 0.98/0.02 = 49, full = (0.05×49 - 0.95)/49 = (2.45-0.95)/49 = 0.0306
# Quarter = 0.0306 × 0.25 = 0.00765
# Shares = floor(1000 × 0.00765 / 0.02) = floor(382.6) = 382
expected_b_odds = 0.98 / 0.02
expected_full_kelly = (0.05 * expected_b_odds - 0.95) / expected_b_odds
expected_quarter_kelly = expected_full_kelly * 0.25
expected_shares = int(1000 * expected_quarter_kelly / 0.02)
assert sig_b.shares == expected_shares
print(f"  Market B: {sig_b.shares} shares (calculado={expected_shares}) ✓")


print("\n✅ Todos os testes do strategy engine passaram!")
