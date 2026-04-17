"""
Testes do telegram_bot.py — formatação e dispatch (sem enviar de verdade).

Cenários:
  1. format_entry com TradeSignal
  2. format_exit (TP, SL, bounce_exit, resolução)
  3. format_bounce
  4. format_error
  5. format_scan_result
  6. send_message com token placeholder → retorna False sem chamar API
  7. TelegramNotifier.notify_entries → conta mensagens
  8. TelegramNotifier.notify_monitor_events → dispatch por tipo
  9. Escape de caracteres Markdown
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch
from strategy import TradeSignal
from paper_engine import ExecutionResult
from monitor import MonitorEvent, MonitorResult
from telegram_bot import (
    format_entry,
    format_exit,
    format_bounce,
    format_resolution,
    format_error,
    format_scan_result,
    send_message,
    TelegramNotifier,
    _escape,
)


# ─── Bloco 1: format_entry ──────────────────────────────────────────────────

print("=== Bloco 1: format_entry ===")

signal = TradeSignal(
    market_id="mkt_1", condition_id="0xc1", event_id="evt_1",
    question="Will BTC hit $100k?", side="YES", token_id="0xtok",
    entry_price=0.03, ev=0.02, ev_pct=0.667, shares=100, cost=3.00,
    target_exit=0.09, stop_price=0.015, bounce_exit_pct=None,
    category="crypto", strategy_name="penny",
)
result = ExecutionResult(success=True, position_id=1, message="ok", signal=signal)

text = format_entry(result)
assert "Nova Entrada" in text
assert "YES" in text
assert "100 shares" in text
assert "penny" in text
assert "crypto" in text
print(text)
print("  ✓")


# ─── Bloco 2: format_exit ───────────────────────────────────────────────────

print("\n=== Bloco 2: format_exit ===")

# Take profit
tp_event = MonitorEvent(
    event_type="exit", position_id=1, market_id="mkt_1",
    question="Will BTC hit $100k?",
    details={"reason": "take_profit", "side": "YES", "strategy": "penny",
             "entry_price": 0.03, "exit_price": 0.09, "pnl": 6.0},
)
text = format_exit(tp_event)
assert "Take Profit" in text
assert "6.00" in text
assert "✅" in text
print(text)
print("  TP ✓")

# Stop loss
sl_event = MonitorEvent(
    event_type="exit", position_id=2, market_id="mkt_2",
    question="Lakers win?",
    details={"reason": "stop_loss", "side": "YES", "strategy": "penny",
             "entry_price": 0.03, "exit_price": 0.015, "pnl": -1.5},
)
text = format_exit(sl_event)
assert "Stop Loss" in text
assert "❌" in text
print(f"  SL ✓")

# Bounce exit
be_event = MonitorEvent(
    event_type="exit", position_id=3, market_id="mkt_3",
    question="Fed cuts?",
    details={"reason": "bounce_exit", "side": "NO", "strategy": "no_systematic",
             "entry_price": 0.30, "exit_price": 0.40, "pnl": 5.0},
)
text = format_exit(be_event)
assert "Bounce Exit" in text
print(f"  Bounce exit ✓")


# ─── Bloco 3: format_bounce ─────────────────────────────────────────────────

print("\n=== Bloco 3: format_bounce ===")

bounce = MonitorEvent(
    event_type="bounce", position_id=1, market_id="mkt_1",
    question="BTC 100k?",
    details={"direction": "UP", "old_price": 0.03, "new_price": 0.05,
             "change_pct": 0.667, "side": "YES", "strategy": "penny"},
)
text = format_bounce(bounce)
assert "Bounce UP" in text
assert "67%" in text
assert "🔺" in text
print(text)
print("  ✓")

# Bounce down
bounce_dn = MonitorEvent(
    event_type="bounce", position_id=2, market_id="mkt_2",
    question="Lakers?",
    details={"direction": "DOWN", "old_price": 0.05, "new_price": 0.03,
             "change_pct": 0.40, "side": "YES", "strategy": "penny"},
)
text = format_bounce(bounce_dn)
assert "🔻" in text
assert "DOWN" in text
print(f"  Bounce DOWN ✓")


# ─── Bloco 4: format_resolution ─────────────────────────────────────────────

print("\n=== Bloco 4: format_resolution ===")

res_win = MonitorEvent(
    event_type="resolution", position_id=1, market_id="mkt_1",
    question="BTC 100k?",
    details={"resolution": "1", "side": "YES", "exit_price": 1.0,
             "pnl": 97.0, "reason": "resolved_win"},
)
text = format_resolution(res_win)
assert "Resolvido" in text
assert "🏆" in text
assert "97.00" in text
print(text)
print("  ✓")


# ─── Bloco 5: format_error e format_scan_result ─────────────────────────────

print("\n=== Bloco 5: error e scan ===")

text = format_error("Falha ao buscar preços: timeout")
assert "Erro" in text
assert "timeout" in text
print(f"  Error: OK ✓")

text = format_scan_result("Scan: 150 mercados | crypto=80, sports=70", 5)
assert "Scan Completo" in text
assert "5 novas entradas" in text
print(f"  Scan: OK ✓")


# ─── Bloco 6: send_message com placeholder → False ───────────────────────────

print("\n=== Bloco 6: send_message sem config ===")

# Token é "YOUR_TOKEN_HERE" → deve retornar False sem fazer request
result = send_message("teste")
assert result is False
print("  Token placeholder → False (sem request) ✓")


# ─── Bloco 7: TelegramNotifier.notify_entries ───────────────────────────────

print("\n=== Bloco 7: notify_entries ===")

notifier = TelegramNotifier()

results = [
    ExecutionResult(success=True, position_id=1, message="ok", signal=signal),
    ExecutionResult(success=False, position_id=None, message="falhou"),
    ExecutionResult(success=True, position_id=2, message="ok", signal=signal),
]

# Mock send_message para contar chamadas
with patch("telegram_bot.send_message", return_value=True) as mock:
    sent = notifier.notify_entries(results)

assert sent == 2  # 2 success, 1 failure
assert mock.call_count == 2
print(f"  {sent} mensagens enviadas de {len(results)} results ✓")


# ─── Bloco 8: notify_monitor_events ─────────────────────────────────────────

print("\n=== Bloco 8: notify_monitor_events ===")

monitor_result = MonitorResult(
    events=[
        tp_event,       # exit
        bounce,         # bounce
        res_win,        # resolution
    ],
    errors=["Falha de rede"],
)

with patch("telegram_bot.send_message", return_value=True) as mock:
    sent = notifier.notify_monitor_events(monitor_result)

# 3 events + 1 error = 4 mensagens
assert sent == 4
assert mock.call_count == 4
print(f"  {sent} mensagens (3 events + 1 error) ✓")


# ─── Bloco 9: escape de caracteres ──────────────────────────────────────────

print("\n=== Bloco 9: escape Markdown ===")

escaped = _escape("Will BTC hit $100k? (yes/no) [test]")
assert "\\" in escaped
assert "\\[" in escaped
assert "\\(" in escaped
# ? não é caractere especial do Markdown — não deve ser escapado
assert "\\?" not in escaped
print(f"  '{escaped[:40]}...' ✓")

# Sem caracteres especiais → não muda
clean = _escape("simple text")
assert clean == "simple text"
print(f"  Texto limpo não muda ✓")


print("\n✅ Todos os testes do telegram_bot passaram!")
