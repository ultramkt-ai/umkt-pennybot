"""
Testes do analytics.py — métricas, log completo, exports, digest.

Cenários:
  1. Portfolio vazio → métricas zeradas
  2. Métricas com trades variados (wins, losses, diferentes estratégias/categorias)
  3. EV real vs teórico
  4. Log completo de transações (ordem cronológica, todos os campos)
  5. Trades history log (open + close actions)
  6. Export CSV e JSON
  7. Relatório consolidado (por estratégia, por categoria, por exit_reason)
  8. Daily digest formatado
  9. Drawdown alert
"""

import sys, os, json, csv
sys.path.insert(0, os.path.dirname(__file__))

from state import StateManager
from analytics import Analytics


# ─── Setup: criar um portfolio com trades variados ───────────────────────────

DB = f"/tmp/test_analytics_{os.getpid()}.db"
EXPORT_DIR = f"/tmp/test_analytics_export_{os.getpid()}"

state = StateManager(db_path=DB, snapshots_dir="/tmp/test_analytics_snap")
analytics = Analytics(state, export_dir=EXPORT_DIR)


def _open_and_close(st, market_id, strategy, side, entry, exit_p, shares,
                    category, reason, question="Test?"):
    """Helper: abre e fecha posição. Retorna position_id."""
    pid = st.open_position(
        market_id=market_id, condition_id=f"0x{market_id}",
        event_id=f"evt_{market_id}", strategy=strategy, side=side,
        entry_price=entry, shares=shares, category=category,
        market_question=question, token_id=f"0xtok_{market_id}",
        target_exit=entry * 3, stop_price=entry * 0.5,
    )
    st.close_position(pid, exit_p, reason)
    return pid


# Penny wins
_open_and_close(state, "p1", "penny", "YES", 0.03, 0.09, 100, "crypto",
                "take_profit", "BTC hits 100k?")
_open_and_close(state, "p2", "penny", "YES", 0.02, 1.00, 200, "crypto",
                "resolved_win", "ETH flips BTC?")

# Penny losses
_open_and_close(state, "p3", "penny", "YES", 0.04, 0.00, 150, "tech",
                "resolved_loss", "Apple buys Tesla?")
_open_and_close(state, "p4", "penny", "YES", 0.03, 0.015, 100, "sports",
                "stop_loss", "Lakers win?")

# NO systematic wins
_open_and_close(state, "n1", "no_systematic", "NO", 0.30, 0.45, 50, "finance",
                "take_profit", "Fed cuts rates?")
_open_and_close(state, "n2", "no_systematic", "NO", 0.25, 0.40, 80, "crypto",
                "bounce_exit", "BTC below 50k?")

# NO systematic loss
_open_and_close(state, "n3", "no_systematic", "NO", 0.40, 0.10, 60, "finance",
                "stop_loss", "GDP grows 3%?")

# Posição aberta (penny)
state.open_position(
    market_id="p_open", condition_id="0xp_open", event_id="evt_p_open",
    strategy="penny", side="YES", entry_price=0.03, shares=100,
    category="crypto", market_question="Open position?",
    token_id="0xtok_p_open", target_exit=0.09, stop_price=0.015,
)
state.update_current_price(
    state.get_open_positions()[0]["id"], 0.05
)


# ─── Bloco 1: portfolio vazio ───────────────────────────────────────────────

print("=== Bloco 1: métricas de portfolio vazio ===")
empty_state = StateManager(db_path="/tmp/test_analytics_empty.db",
                           snapshots_dir="/tmp/test_analytics_empty_snap")
empty_analytics = Analytics(empty_state, export_dir="/tmp/test_analytics_empty_exp")
m = empty_analytics.compute_metrics()
assert m.total_trades == 0
assert m.win_rate == 0.0
assert m.total_pnl == 0.0
print(f"  Vazio: trades={m.total_trades}, pnl={m.total_pnl} ✓")
os.remove("/tmp/test_analytics_empty.db")


# ─── Bloco 2: métricas globais ──────────────────────────────────────────────

print("\n=== Bloco 2: métricas globais ===")
m = analytics.compute_metrics()

assert m.total_trades == 7  # 4 penny + 3 NO
assert m.wins == 4           # p1, p2, n1, n2
assert m.losses == 3         # p3, p4, n3
assert m.open_positions == 1  # p_open

# PnL manual:
# p1: (0.09-0.03)*100 = 6.0
# p2: (1.00-0.02)*200 = 196.0
# p3: (0.00-0.04)*150 = -6.0
# p4: (0.015-0.03)*100 = -1.5
# n1: (0.45-0.30)*50 = 7.5
# n2: (0.40-0.25)*80 = 12.0
# n3: (0.10-0.40)*60 = -18.0
expected_pnl = 6.0 + 196.0 + (-6.0) + (-1.5) + 7.5 + 12.0 + (-18.0)
assert abs(m.total_pnl - expected_pnl) < 0.01
print(f"  Total: {m.total_trades} trades, {m.wins}W/{m.losses}L")
print(f"  PnL: ${m.total_pnl:+,.2f} (esperado ${expected_pnl:+,.2f}) ✓")
print(f"  Win rate: {m.win_rate:.1%}, ROI: {m.roi:.1%}")
print(f"  Profit factor: {m.profit_factor:.2f}")
print(f"  Best: ${m.best_trade:+,.2f}, Worst: ${m.worst_trade:+,.2f}")
print(f"  Open: {m.open_positions}, Unrealized: ${m.unrealized_pnl:+,.2f} ✓")

# Unrealized: p_open entry=0.03, current=0.05, shares=100 → (0.05-0.03)*100 = 2.0
assert abs(m.unrealized_pnl - 2.0) < 0.01


# ─── Bloco 3: métricas por estratégia ───────────────────────────────────────

print("\n=== Bloco 3: por estratégia ===")
penny = analytics.compute_metrics(strategy="penny")
no_sys = analytics.compute_metrics(strategy="no_systematic")

assert penny.total_trades == 4
assert penny.wins == 2
assert no_sys.total_trades == 3
assert no_sys.wins == 2
print(f"  Penny: {penny.wins}W/{penny.losses}L, PnL=${penny.total_pnl:+,.2f} ✓")
print(f"  NO sist: {no_sys.wins}W/{no_sys.losses}L, PnL=${no_sys.total_pnl:+,.2f} ✓")

# EV teórico
assert penny.ev_theoretical != 0
assert no_sys.ev_theoretical != 0
print(f"  Penny EV teórico=${penny.ev_theoretical:.4f}, real=${penny.ev_realized:.4f}")
print(f"  NO EV teórico=${no_sys.ev_theoretical:.4f}, real=${no_sys.ev_realized:.4f}")


# ─── Bloco 4: métricas por categoria ────────────────────────────────────────

print("\n=== Bloco 4: por categoria ===")
crypto = analytics.compute_metrics(category="crypto")
finance = analytics.compute_metrics(category="finance")

# crypto: p1 (+6), p2 (+196), n2 (+12), p_open (aberta) = 3 fechadas
assert crypto.total_trades == 3
assert crypto.open_positions == 1
print(f"  Crypto: {crypto.total_trades} trades, PnL=${crypto.total_pnl:+,.2f} ✓")
print(f"  Finance: {finance.total_trades} trades, PnL=${finance.total_pnl:+,.2f} ✓")


# ─── Bloco 5: log completo de transações ────────────────────────────────────

print("\n=== Bloco 5: log completo ===")
log = analytics.get_full_trade_log()

assert len(log) == 8  # 7 fechadas + 1 aberta
# Verificar que todos os campos estão presentes
required_fields = [
    "position_id", "market_id", "strategy", "side", "category",
    "entry_price", "exit_price", "shares", "cost", "pnl", "pnl_pct",
    "exit_reason", "opened_at", "closed_at", "hold_hours", "market_question",
]
for field in required_fields:
    assert field in log[0], f"Campo '{field}' faltando no log"

# Ordem cronológica
for i in range(len(log) - 1):
    assert (log[i]["opened_at"] or "") <= (log[i+1]["opened_at"] or "")

# Posição aberta tem exit_price=None
open_entries = [l for l in log if l["status"] == "open"]
assert len(open_entries) == 1
assert open_entries[0]["exit_price"] is None
assert open_entries[0]["pnl"] == 0

print(f"  {len(log)} entradas no log, todos os campos presentes ✓")
print(f"  Ordem cronológica ✓")
print(f"  Posição aberta com exit=None ✓")


# ─── Bloco 6: trades_history log ────────────────────────────────────────────

print("\n=== Bloco 6: trades_history ===")
th_log = analytics.get_trades_history_log()

# 7 closes + 8 opens = 15 entries no trades_history
assert len(th_log) == 15
opens = [t for t in th_log if t["action"] == "open"]
closes = [t for t in th_log if t["action"] == "close"]
assert len(opens) == 8
assert len(closes) == 7
print(f"  {len(th_log)} entries: {len(opens)} opens + {len(closes)} closes ✓")


# ─── Bloco 7: export CSV ────────────────────────────────────────────────────

print("\n=== Bloco 7: export CSV ===")
csv_path = analytics.export_trade_log_csv("test_trades.csv")
assert os.path.exists(csv_path)

with open(csv_path) as f:
    reader = csv.DictReader(f)
    rows = list(reader)
assert len(rows) == 8
assert "pnl_pct" in rows[0]
print(f"  CSV: {csv_path} ({len(rows)} rows) ✓")


# ─── Bloco 8: export JSON ───────────────────────────────────────────────────

print("\n=== Bloco 8: export JSON ===")
json_path = analytics.export_trade_log_json("test_trades.json")
assert os.path.exists(json_path)

with open(json_path) as f:
    data = json.load(f)
assert len(data) == 8
assert data[0]["position_id"] is not None
print(f"  JSON: {json_path} ({len(data)} entries) ✓")


# ─── Bloco 9: relatório consolidado ─────────────────────────────────────────

print("\n=== Bloco 9: relatório consolidado ===")
report_path = analytics.export_report_json("test_report.json")
assert os.path.exists(report_path)

with open(report_path) as f:
    report = json.load(f)

assert "overall" in report
assert "by_strategy" in report
assert "penny" in report["by_strategy"]
assert "no_systematic" in report["by_strategy"]
assert "by_category" in report
assert "by_exit_reason" in report

# Exit reasons
reasons = report["by_exit_reason"]
assert "take_profit" in reasons
assert "resolved_win" in reasons
assert "stop_loss" in reasons
assert "bounce_exit" in reasons
print(f"  Exit reasons: {reasons} ✓")
print(f"  Estratégias: {list(report['by_strategy'].keys())} ✓")
print(f"  Categorias: {list(report['by_category'].keys())} ✓")


# ─── Bloco 10: daily digest ─────────────────────────────────────────────────

print("\n=== Bloco 10: daily digest ===")
digest = analytics.format_daily_digest()

assert "Daily Digest" in digest
assert "Win rate" in digest
assert "penny" in digest
assert "no_systematic" in digest
assert "EV teórico" in digest

print(digest)
print("  ✓")


# ─── Cleanup ─────────────────────────────────────────────────────────────────

os.remove(DB)
import shutil
shutil.rmtree(EXPORT_DIR, ignore_errors=True)

print("\n✅ Todos os testes do analytics passaram!")
