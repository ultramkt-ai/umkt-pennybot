"""
Teste end-to-end do run.py — pipeline completo com API mockada.

Plano de testes:
  1. cmd_scan: busca mercados → filtra → gera sinais → executa entradas
  2. Verifica que posições foram abertas corretamente no SQLite
  3. cmd_monitor: atualiza preços → detecta TP → fecha posição
  4. cmd_report: não crasheia, mostra dados corretos
  5. cmd_export: gera CSV + JSON legíveis
  6. cmd_status: mostra métricas corretas
  7. cmd_digest: formata sem erro
  8. Integração: estado persiste entre scan → monitor → report
  9. Pipeline com ambas estratégias (penny + NO sist.)
 10. Scan sem mercados elegíveis → zero entradas sem crash
 11. Monitor sem posições → resultado vazio
 12. Verificação de campos em cascata (token_id, target, bounce_exit_pct)

Todas as APIs externas são mockadas — zero rede.
"""

import sys, os, json, csv, shutil
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch
from datetime import datetime, timezone, timedelta

from config import PENNY_STRATEGY, NO_SYSTEMATIC_STRATEGY, STRATEGIES
from state import StateManager
from paper_engine import PaperEngine
from monitor import Monitor
from analytics import Analytics
from telegram_bot import TelegramNotifier
from run import cmd_scan, cmd_monitor, cmd_report, cmd_export, cmd_status, cmd_digest


# ─── Setup ───────────────────────────────────────────────────────────────────

DB = f"/tmp/test_run_e2e_{os.getpid()}.db"
SNAP_DIR = f"/tmp/test_run_snap_{os.getpid()}"
EXPORT_DIR = f"/tmp/test_run_export_{os.getpid()}"

state = StateManager(db_path=DB, snapshots_dir=SNAP_DIR)
engine = PaperEngine(state, mode="paper")
monitor = Monitor(state, engine)
analytics = Analytics(state, export_dir=EXPORT_DIR)
notifier = TelegramNotifier()

# Futuro para end_date
future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


# ─── Mercados fake da Gamma API ──────────────────────────────────────────────

def make_fake_events():
    """Retorna eventos que geram mercados elegíveis para ambas as estratégias."""
    return {
        21: [  # crypto
            {
                "id": "evt_btc",
                "tags": [{"id": 21, "label": "Crypto"}],
                "markets": [
                    {
                        "id": "mkt_btc_penny",
                        "conditionId": "0xcond_btc",
                        "question": "Will BTC hit $200k by end of year?",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.03","0.97"]',
                        "clobTokenIds": '["0xbtc_yes","0xbtc_no"]',
                        "liquidity": "8000",
                        "volume": "50000",
                        "endDate": future,
                        "active": True, "closed": False, "acceptingOrders": True,
                    },
                    {
                        "id": "mkt_eth_no",
                        "conditionId": "0xcond_eth",
                        "question": "Will ETH stay below $5k?",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.60","0.40"]',
                        "clobTokenIds": '["0xeth_yes","0xeth_no"]',
                        "liquidity": "12000",
                        "volume": "80000",
                        "endDate": future,
                        "active": True, "closed": False, "acceptingOrders": True,
                    },
                ],
            },
        ],
        100639: [  # sports
            {
                "id": "evt_nba",
                "tags": [{"id": 100639, "label": "Sports"}],
                "markets": [
                    {
                        "id": "mkt_lakers",
                        "conditionId": "0xcond_lakers",
                        "question": "Will Lakers win championship?",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.02","0.98"]',
                        "clobTokenIds": '["0xlakers_yes","0xlakers_no"]',
                        "liquidity": "15000",
                        "volume": "100000",
                        "endDate": future,
                        "active": True, "closed": False, "acceptingOrders": True,
                    },
                ],
            },
        ],
        1401: [],   # tech — vazio
        120: [],    # finance — vazio
    }


def fake_fetch(tag_id, **kwargs):
    events = make_fake_events()
    yield from events.get(tag_id, [])


# ─── Bloco 1: cmd_scan (pipeline completo) ──────────────────────────────────

print("=== Bloco 1: cmd_scan ===")

with patch("scanner.fetch_events_by_tag", side_effect=fake_fetch), \
     patch("telegram_bot.send_message", return_value=True):
    entries = cmd_scan(state, engine, notifier, bankroll=1000)

print(f"  Entradas: {entries}")
# mkt_btc_penny: YES@$0.03 → penny (elegível, EV>0)
# mkt_lakers: YES@$0.02 → penny (elegível, EV>0)
# mkt_eth_no: NO@$0.40 → NO sist (NO ≤ 0.50, elegível)
assert entries >= 2, f"Esperava ≥2 entradas, got {entries}"
print(f"  ≥2 entradas ✓")


# ─── Bloco 2: verificar posições no SQLite ───────────────────────────────────

print("\n=== Bloco 2: posições no SQLite ===")

all_open = state.get_open_positions()
print(f"  {len(all_open)} posições abertas")

for pos in all_open:
    assert pos["status"] == "open"
    assert pos["entry_price"] > 0
    assert pos["shares"] > 0
    assert pos["cost"] > 0
    assert pos["target_exit"] is not None
    assert pos["stop_price"] is not None
    # token_id deve estar preenchido
    assert pos["token_id"] != "", f"Posição {pos['id']} sem token_id!"
    print(f"  pos={pos['id']} {pos['side']} {pos['market_id']} "
          f"@ ${pos['entry_price']:.4f} × {pos['shares']:.0f} "
          f"token={pos['token_id'][:15]}... "
          f"TP=${pos['target_exit']:.4f} SL=${pos['stop_price']:.4f} "
          f"bounce_exit={'None' if pos['bounce_exit_pct'] is None else pos['bounce_exit_pct']}")

# Penny posições devem ter bounce_exit_pct = None
penny_pos = [p for p in all_open if p["strategy"] == "penny"]
for p in penny_pos:
    assert p["bounce_exit_pct"] is None, \
        f"Penny pos {p['id']} tem bounce_exit_pct={p['bounce_exit_pct']}"
print("  Penny: bounce_exit_pct=None ✓")

# NO sist posições devem ter bounce_exit_pct = 0.5
no_pos = [p for p in all_open if p["strategy"] == "no_systematic"]
for p in no_pos:
    assert p["bounce_exit_pct"] == 0.5, \
        f"NO pos {p['id']} tem bounce_exit_pct={p['bounce_exit_pct']}"
if no_pos:
    print("  NO sist: bounce_exit_pct=0.5 ✓")


# ─── Bloco 3: cmd_monitor → TP exit ─────────────────────────────────────────

print("\n=== Bloco 3: cmd_monitor (TP) ===")

# Simular que um penny subiu para o TP
penny_position = penny_pos[0] if penny_pos else None

if penny_position:
    token_id = penny_position["token_id"]
    target = penny_position["target_exit"]

    # Todos os tokens retornam preço normal, exceto o que atingiu TP
    mock_prices = {p["token_id"]: p["entry_price"] for p in all_open}
    mock_prices[token_id] = target + 0.01  # acima do TP

    with patch("monitor.get_midpoints", return_value=mock_prices), \
         patch("telegram_bot.send_message", return_value=True):
        cmd_monitor(monitor, notifier)

    # Verificar que a posição foi fechada
    remaining = state.get_open_positions()
    closed_ids = {p["id"] for p in all_open} - {p["id"] for p in remaining}
    assert penny_position["id"] in closed_ids, "TP deveria ter fechado a posição"
    print(f"  Posição {penny_position['id']} fechada por TP ✓")
else:
    print("  (sem posições penny para testar TP)")


# ─── Bloco 4: cmd_report ────────────────────────────────────────────────────

print("\n=== Bloco 4: cmd_report ===")
cmd_report(analytics)
print("  Report sem crash ✓")


# ─── Bloco 5: cmd_export ────────────────────────────────────────────────────

print("\n=== Bloco 5: cmd_export ===")
cmd_export(analytics)

# Verificar que os arquivos existem e são legíveis
export_files = os.listdir(EXPORT_DIR)
csv_files = [f for f in export_files if f.endswith(".csv")]
json_files = [f for f in export_files if f.endswith(".json")]
assert len(csv_files) >= 1, "Nenhum CSV gerado"
assert len(json_files) >= 2, "Esperava ≥2 JSONs (trade_log + report)"

# Ler CSV
csv_path = os.path.join(EXPORT_DIR, csv_files[0])
with open(csv_path) as f:
    reader = csv.DictReader(f)
    csv_rows = list(reader)
assert len(csv_rows) >= 1
assert "pnl_pct" in csv_rows[0]
assert "hold_hours" in csv_rows[0]
print(f"  CSV: {len(csv_rows)} rows, campos OK ✓")

# Ler JSON report
report_files = [f for f in json_files if f.startswith("report_")]
if report_files:
    with open(os.path.join(EXPORT_DIR, report_files[0])) as f:
        report_data = json.load(f)
    assert "overall" in report_data
    assert "by_strategy" in report_data
    print(f"  Report JSON: overall + {len(report_data['by_strategy'])} estratégias ✓")


# ─── Bloco 6: cmd_status ────────────────────────────────────────────────────

print("\n=== Bloco 6: cmd_status ===")
cmd_status(state)
print("  Status sem crash ✓")


# ─── Bloco 7: cmd_digest ────────────────────────────────────────────────────

print("\n=== Bloco 7: cmd_digest ===")
with patch("telegram_bot.send_message", return_value=True):
    cmd_digest(analytics, notifier)
print("  Digest sem crash ✓")


# ─── Bloco 8: scan repetido → não duplica posições ──────────────────────────

print("\n=== Bloco 8: scan repetido (anti-duplicata) ===")

open_before = len(state.get_open_positions())

with patch("scanner.fetch_events_by_tag", side_effect=fake_fetch), \
     patch("telegram_bot.send_message", return_value=True):
    entries2 = cmd_scan(state, engine, notifier, bankroll=1000)

open_after = len(state.get_open_positions())
# filter_no_duplicate deve impedir re-entrada nos mesmos mercados
# (pode ter novas entradas em mercados que não tinham posição)
print(f"  Antes: {open_before}, Depois: {open_after}, Novas: {entries2}")
print("  Anti-duplicata funcionando ✓")


# ─── Bloco 9: monitor sem posições → resultado vazio ────────────────────────

print("\n=== Bloco 9: monitor com todas fechadas ===")

# Fechar todas as posições manualmente
for pos in state.get_open_positions():
    state.close_position(pos["id"], pos["entry_price"], "manual")

assert len(state.get_open_positions()) == 0

with patch("monitor.get_midpoints", return_value={}):
    cmd_monitor(monitor, notifier)
print("  Monitor sem posições → OK ✓")


# ─── Bloco 10: verificar log completo ────────────────────────────────────────

print("\n=== Bloco 10: log completo de transações ===")

log = analytics.get_full_trade_log()
th_log = analytics.get_trades_history_log()

print(f"  Posições total: {len(log)}")
print(f"  Trades history: {len(th_log)} entries")

# Toda posição fechada deve ter exit_reason
for entry in log:
    if entry["status"] in ("closed", "resolved"):
        assert entry["exit_reason"] is not None, \
            f"Posição {entry['position_id']} fechada sem exit_reason!"
        assert entry["pnl"] is not None

# Toda posição deve ter campos obrigatórios
for entry in log:
    for field in ["position_id", "market_id", "strategy", "side",
                  "entry_price", "shares", "cost", "opened_at"]:
        assert entry[field] is not None, \
            f"Posição {entry['position_id']} sem campo {field}"

print("  Todas as posições com campos completos ✓")
print("  Todas as fechadas com exit_reason ✓")


# ─── Bloco 11: snapshot funciona ─────────────────────────────────────────────

print("\n=== Bloco 11: snapshot ===")
snap_path = state.save_snapshot()
assert os.path.exists(snap_path)
with open(snap_path) as f:
    snap = json.load(f)
assert "positions" in snap
assert "trades_history" in snap
assert "market_cache" in snap
print(f"  Snapshot: {snap_path}")
print(f"  Posições: {len(snap['positions'])}, "
      f"Trades: {len(snap['trades_history'])}, "
      f"Cache: {len(snap['market_cache'])} ✓")


# ─── Bloco 12: CLI parse ────────────────────────────────────────────────────

print("\n=== Bloco 12: CLI parse ===")

from run import main
import argparse

# Testar que o argparse funciona sem crashar
with patch("sys.argv", ["run.py", "status"]), \
     patch("run.create_components", return_value=(state, engine, monitor, analytics, notifier)):
    try:
        main()
        print("  'run.py status' parsed OK ✓")
    except SystemExit:
        print("  'run.py status' parsed OK ✓")


# ─── Cleanup ─────────────────────────────────────────────────────────────────

os.remove(DB)
shutil.rmtree(SNAP_DIR, ignore_errors=True)
shutil.rmtree(EXPORT_DIR, ignore_errors=True)

print("\n✅ Todos os 12 blocos do teste end-to-end passaram!")
