# Copytrading Espelho - Guia de Operação

## Visão Geral

O Penny-Bot agora replica **exatamente** a carteira alvo (`0xa445c59c0531d28a13550f29d734b33520530286`), tanto nas entradas quanto nas saídas.

**Polling:** 60 segundos  
**Modo:** `MIRROR_STRICT=True` (replica automaticamente)  
**Dashboard:** `http://127.0.0.1:5000` (aba "Copytrading Mirror")

---

## Quick Start

### 1. Configurar Variáveis de Ambiente

```bash
# ~/.openclaw/workspace/penny-bot/.env
PENNY_BOT_WALLET_ADDRESS=0xa445c59c0531d28a13550f29d734b33520530286
MIRROR_STRICT=true
WALLET_MONITOR_INTERVAL_SECONDS=60
MAX_POSITIONS_PER_WALLET=200
BOT_MODE=paper
```

### 2. Instalar Dependências

```bash
cd ~/Downloads/penny-bot
pip3 install -r requirements.txt
```

### 3. Rodar o Bot

```bash
# Terminal 1: Bot principal (scanner + strategy + monitor + copytrading)
python3 main.py

# Terminal 2: Dashboard (opcional)
python3 dashboard.py
# → http://127.0.0.1:5000
```

---

## O Que Acontece

### Entradas (Mirror Entry)

Quando a wallet abre uma posição:

```
Wallet: [ABC YES × 500 @ $0.20]
  ↓ (60s)
Bot: [ABC YES × 500 @ $0.20]  ← mesma qtd, mesmo preço, mesmo side
```

**Strategy:** `mirror_copy` (não usa EV/kelly — é espelho puro)  
**Categoria:** `mirror`

### Saídas (Mirror Exit)

Quando a wallet fecha uma posição:

```
Wallet: fecha [ABC YES] @ $0.08
  ↓ (60s)
Bot: fecha [ABC YES] @ $0.08 (preço de mercado atual)
  → PnL registrado
  → Slippage calculado (wallet vs bot)
```

**Exit Reason:** `wallet_exit`

### Ajustes (Mirror Adjust)

Quando a wallet ajusta shares:

```
Wallet: [XYZ NO] 2000 → 1000 shares (-50%)
  ↓ (60s)
Bot: [XYZ NO] 2000 → 1000 shares
  → Vende 1000 shares @ preço atual
  → PnL parcial realizado
```

**Action:** `adjust_down` ou `adjust_up`

---

## Dashboard

Acesse `http://127.0.0.1:5000` e role até a aba **"🦞 Copytrading Mirror"**.

**Mostra:**
- Wallet address
- Posições copiadas abertas
- Total de trades copiados
- PnL total de copytrading
- Win rate
- Últimos 20 eventos (entries, exits, adjusts)
- Last sync timestamp

**API:** `GET http://127.0.0.1:5000/api/copytrading`

---

## Logs

### Entradas
```
2026-04-17 00:15:42 [INFO] MIRROR ENTRY: pos=789 YES abc123 × 500 @ $0.20 = $100.00
```

### Saídas
```
2026-04-17 00:16:42 [INFO] MIRROR EXIT: pos=123 YES @ $0.03 → $0.08 | PnL=+$50.00 | slippage=0.0%
```

### Ajustes
```
2026-04-17 00:17:42 [INFO] MIRROR ADJUST DOWN: pos=456 NO xyz123 -1000 shares @ $0.35 | PnL=+$50.00
```

### Slippage Alert (>5%)
```
2026-04-17 00:18:42 [WARNING] SLIPPAGE ALERT: pos=123 wallet=$0.08 bot=$0.075 (-6.2%)
```

---

## Auditoria

### Snapshots JSON

Salvos automaticamente quando há ações de copytrading:

```bash
data/snapshots/snapshot_20260417_001542.json
```

### Trade Log CSV

Exporta todas as posições com strategy=`mirror_copy`:

```bash
python3 -c "from analytics import Analytics; from state import StateManager; \
state = StateManager(); a = Analytics(state); print(a.export_trade_log_csv())"
```

### Query Direta (SQLite)

```bash
sqlite3 data/positions.db "SELECT * FROM positions WHERE strategy='mirror_copy';"
sqlite3 data/positions.db "SELECT * FROM positions WHERE exit_reason='wallet_exit';"
sqlite3 data/positions.db "SELECT * FROM trades_history WHERE reason='mirror_copy' OR action IN ('adjust_up', 'adjust_down');"
```

---

## Slippage Tracking

O slippage é calculado quando:
- Wallet exit price está disponível (futuro: capturar do polling)
- Bot exit price é o preço de mercado no momento do exit

**Threshold de alerta:** >5%

**Causas comuns:**
- Lag de polling (60s)
- Liquidez baixa no token
- Volatilidade alta

**Se slippage consistently >5%:**
- Reduzir `WALLET_MONITOR_INTERVAL_SECONDS` para 30s
- Verificar liquidez dos mercados copiados

---

## Limites e Segurança

### Teto de Posições

```bash
MAX_POSITIONS_PER_WALLET=200
```

Se atingido, copytrading é pausado até posições serem fechadas.

### Modo Revisão (Dry Run)

Para testar sem executar:

```bash
MIRROR_STRICT=false
```

Neste modo, o bot **só alerta**, não executa entradas/saídas/ajustes.

### Modo Paper vs Live

- **Paper:** `BOT_MODE=paper` (default) — simula tudo no SQLite
- **Live:** `BOT_MODE=live` — executaria ordens reais na CLOB (não implementado ainda)

---

## Multi-Wallet (Futuro)

Hoje: 1 wallet por instância do bot.

Para copiar múltiplas wallets (ex: penny + BTC micro):

**Opção 1:** Rodar múltiplas instâncias
```bash
# Instância 1: Penny wallet
PENNY_BOT_WALLET_ADDRESS=0xa445c... python3 main.py

# Instância 2: BTC wallet
PENNY_BOT_WALLET_ADDRESS=0xdE17f... python3 main.py
```

**Opção 2:** Extender `wallet_monitor.py` para poller múltiplas wallets (TODO)

---

## Troubleshooting

### Bot não está copiando entradas

1. Check `MIRROR_STRICT=true` no `.env`
2. Check wallet address correta
3. Check logs: `grep "MIRROR ENTRY" ~/Downloads/penny-bot/logs/*.log`
4. Check se `get_wallet_positions()` está retornando dados:
   ```bash
   python3 -c "from gamma_client import get_wallet_positions; \
   print(get_wallet_positions('0xa445c59c0531d28a13550f29d734b33520530286'))"
   ```

### Slippage alto consistently

1. Reduzir polling interval para 30s
2. Check liquidez dos mercados copiados
3. Verificar se wallet está usando limit orders vs market orders

### Posições divergentes

1. Check snapshot mais recente: `data/snapshots/snapshot_*.json`
2. Query SQLite para ver estado atual
3. Se necessário, reset manual:
   ```bash
   sqlite3 data/positions.db "DELETE FROM positions WHERE strategy='mirror_copy' AND status='open';"
   ```

---

## Próximos Passos

- [ ] Telegram integration (alertas de copytrading)
- [ ] Capturar wallet exit price real do polling
- [ ] Multi-wallet support
- [ ] Dashboard: gráficos de PnL por wallet
- [ ] Backtesting: performance histórica do copytrading

---

## Referências

- `wallet_monitor.py` — lógica de copytrading
- `gamma_client.py` — `get_wallet_positions()`
- `state.py` — `adjust_position_shares()`
- `COPYTRADING-MIRROR.md` — doc completa da arquitetura
- Dashboard: `http://127.0.0.1:5000`
