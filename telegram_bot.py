"""
telegram_bot.py — Alertas via Telegram.

Tipos de mensagem:
  - Novas entradas (TradeSignal executado)
  - Exits (TP, SL, bounce_exit, resolução — com PnL)
  - Bounces significativos (alerta sem fechar)
  - Daily digest (resumo consolidado)
  - Alertas de risco (drawdown > threshold)
  - Erros do sistema

Usa requests diretamente (sem python-telegram-bot) para manter
dependências leves. A API do Telegram é simples o suficiente.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from paper_engine import ExecutionResult
from monitor import MonitorEvent, MonitorResult


logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# ─── Envio base ──────────────────────────────────────────────────────────────

class TelegramError(Exception):
    """Falha ao enviar mensagem para o Telegram."""


def send_message(
    text: str,
    chat_id: str = TELEGRAM_CHAT_ID,
    parse_mode: str = "Markdown",
    disable_preview: bool = True,
    max_retries: int = 2,
) -> bool:
    """
    Envia mensagem para o Telegram. Retorna True se enviou.

    Não lança exceção em caso de falha — apenas loga e retorna False.
    O bot não deve parar de funcionar porque o Telegram está fora.
    """
    if TELEGRAM_TOKEN == "YOUR_TOKEN_HERE":
        logger.debug("Telegram não configurado — mensagem ignorada")
        return False

    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True

            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            logger.warning(
                "Telegram HTTP %d: %s",
                resp.status_code,
                data.get("description", resp.text[:100]),
            )

            # Rate limit do Telegram — esperar e tentar de novo
            if resp.status_code == 429:
                import time
                retry_after = data.get("parameters", {}).get("retry_after", 5)
                time.sleep(retry_after)
                continue

            return False

        except requests.RequestException as e:
            logger.warning("Telegram erro de rede (tentativa %d): %s", attempt + 1, e)
            if attempt == max_retries - 1:
                return False

    return False


# ─── Formatação de mensagens ─────────────────────────────────────────────────

def format_entry(result: ExecutionResult) -> str:
    """Formata mensagem de nova entrada."""
    if not result.success or result.signal is None:
        return ""

    s = result.signal
    emoji = "🟢" if s.side == "YES" else "🔵"

    return (
        f"{emoji} *Nova Entrada*\n"
        f"\n"
        f"📋 {_escape(s.question)}\n"
        f"💰 {s.side} {s.shares} shares @ ${s.entry_price:.4f}\n"
        f"💵 Custo: ${s.cost:.2f}\n"
        f"📊 EV: {s.ev_pct:.0%} | Strategy: {s.strategy_name}\n"
        f"🎯 TP: ${s.target_exit:.4f} | SL: ${s.stop_price:.4f}\n"
        f"🏷️ {s.category}"
    )


def format_exit(event: MonitorEvent) -> str:
    """Formata mensagem de saída (TP, SL, bounce_exit, resolução)."""
    d = event.details
    reason = d.get("reason", "?")
    pnl = d.get("pnl", 0)

    emoji_map = {
        "take_profit": "🎯",
        "stop_loss": "🛑",
        "bounce_exit": "📈",
        "resolved_win": "🏆",
        "resolved_loss": "💀",
    }
    emoji = emoji_map.get(reason, "🚪")
    pnl_emoji = "✅" if pnl and pnl > 0 else "❌"

    lines = [
        f"{emoji} *Exit: {reason.replace('_', ' ').title()}*",
        f"",
        f"📋 {_escape(event.question)}",
        f"💰 {d.get('side', '?')} @ ${d.get('entry_price', 0):.4f} → ${d.get('exit_price', 0):.4f}",
        f"{pnl_emoji} PnL: ${pnl:+,.2f}",
        f"📊 Strategy: {d.get('strategy', '?')}",
    ]

    return "\n".join(lines)


def format_bounce(event: MonitorEvent) -> str:
    """Formata alerta de bounce (sem fechar posição)."""
    d = event.details
    direction = d.get("direction", "?")
    emoji = "🔺" if direction == "UP" else "🔻"
    change = d.get("change_pct", 0)

    return (
        f"{emoji} *Bounce {direction}* ({change:.0%})\n"
        f"\n"
        f"📋 {_escape(event.question)}\n"
        f"💰 {d.get('side', '?')} ${d.get('old_price', 0):.4f} → ${d.get('new_price', 0):.4f}\n"
        f"📊 {d.get('strategy', '?')}"
    )


def format_resolution(event: MonitorEvent) -> str:
    """Formata resolução de mercado."""
    d = event.details
    reason = d.get("reason", "?")
    pnl = d.get("pnl", 0)
    emoji = "🏆" if "win" in reason else "💀"

    return (
        f"{emoji} *Mercado Resolvido*\n"
        f"\n"
        f"📋 {_escape(event.question)}\n"
        f"{'✅' if pnl and pnl > 0 else '❌'} PnL: ${pnl:+,.2f}\n"
        f"📊 {d.get('side', '?')} @ ${d.get('exit_price', 0):.2f}"
    )


def format_error(message: str) -> str:
    """Formata mensagem de erro do sistema."""
    return f"⚠️ *Erro do Sistema*\n\n{_escape(message)}"


def format_scan_result(summary: str, new_entries: int) -> str:
    """Formata resultado do scan."""
    return (
        f"🔍 *Scan Completo*\n"
        f"\n"
        f"{_escape(summary)}\n"
        f"🆕 {new_entries} novas entradas"
    )


def _escape(text: str) -> str:
    """Escapa caracteres especiais do Markdown do Telegram."""
    for char in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(char, f"\\{char}")
    return text


# ─── Dispatcher de eventos ───────────────────────────────────────────────────

class TelegramNotifier:
    """
    Consome eventos do monitor/scanner e envia mensagens formatadas.

    Uso:
        notifier = TelegramNotifier()
        notifier.notify_entries(execution_results)
        notifier.notify_monitor_events(monitor_result)
        notifier.notify_daily_digest(digest_text)
    """

    def __init__(self, chat_id: str = TELEGRAM_CHAT_ID):
        self.chat_id = chat_id

    def notify_entry(self, result: ExecutionResult) -> bool:
        """Envia alerta de nova entrada."""
        if not result.success:
            return False
        text = format_entry(result)
        if text:
            return send_message(text, self.chat_id)
        return False

    def notify_entries(self, results: list[ExecutionResult]) -> int:
        """Envia alertas de múltiplas entradas. Retorna quantas enviou."""
        sent = 0
        for result in results:
            if self.notify_entry(result):
                sent += 1
        return sent

    def notify_monitor_events(self, monitor_result: MonitorResult) -> int:
        """
        Processa todos os eventos de um ciclo do monitor.
        Envia mensagem formatada para cada um. Retorna quantas enviou.
        """
        sent = 0

        for event in monitor_result.events:
            text = ""
            if event.event_type == "exit":
                text = format_exit(event)
            elif event.event_type == "bounce":
                text = format_bounce(event)
            elif event.event_type == "resolution":
                text = format_resolution(event)

            if text and send_message(text, self.chat_id):
                sent += 1

        # Erros do monitor
        for error in monitor_result.errors:
            if send_message(format_error(error), self.chat_id):
                sent += 1

        return sent

    def notify_daily_digest(self, digest_text: str) -> bool:
        """Envia daily digest (gerado pelo analytics.format_daily_digest)."""
        return send_message(digest_text, self.chat_id)

    def notify_scan(self, summary: str, new_entries: int) -> bool:
        """Envia resultado do scan."""
        return send_message(format_scan_result(summary, new_entries), self.chat_id)

    def notify_error(self, message: str) -> bool:
        """Envia alerta de erro."""
        return send_message(format_error(message), self.chat_id)

    def notify_drawdown(self, drawdown_pct: float) -> bool:
        """Envia alerta de drawdown."""
        text = (
            f"🚨 *DRAWDOWN ALERT*\n"
            f"\n"
            f"Drawdown máximo atingiu {drawdown_pct:.1%}\n"
            f"Verifique as posições abertas."
        )
        return send_message(text, self.chat_id)
