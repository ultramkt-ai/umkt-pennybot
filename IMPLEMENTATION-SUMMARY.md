# ✅ Copytrading Espelho Implementado

## Resumo Executivo

Implementamos **cópia exata da carteira alvo** (`0xa445c59c0531d28a13550f29d734b33520530286`), tanto nas entradas quanto nas saídas.

**Status:** ✅ Produção (modo paper)  
**Polling:** 60 segundos  
**Dashboard:** `http://127.0.0.1:5000` (aba "🦞 Copytrading Mirror")

---

## O Que Foi Entregue

### 1. Mirror Trading Automático

| Ação da Wallet | Reação do Bot | Delay |
|----------------|---------------|-------|
| Abre posição | Abre posição idêntica (mesmos shares, side, preço) | ≤60s |
| Fecha posição | Fecha posição (mesmo market) | ≤60s |
| Aumenta shares | Compra delta shares | ≤60s |
| Reduz shares | Vende delta shares (partial exit) | ≤60s |

### 2. Slippage Tracking

- Compara preço de saída da wallet vs bot
- Alerta se slippage >5%
- Auditoria completa em logs + snapshots

### 3. Dashboard Integration

Nova aba **"🦞 Copytrading Mirror"** mostra:
- Wallet address
- Posições copiadas abertas
- Total de trades copiados
- PnL total de copytrading
- Win rate
- Últimos 20 eventos (entries, exits, adjusts)
- Last sync timestamp

### 4. Reason Codes (Auditoria)

Novos códigos de saída no SQLite:
- `wallet_exit` — wallet origem fechou
- `mirror_adjust` — ajuste de shares (partial exit)
- `mirror_copy` — estratégia de copytrading

---

## Arquivos Criados/Modificados

| Arquivo | Ação | Descrição |
|---------|------|-----------|
| `wallet_monitor.py` | ✨ Reescrito | Copytrading espelho completo (22KB) |
| `gamma_client.py` | 📝 Edit | `get_wallet_positions()` + `WalletPosition` |
| `state.py` | 📝 Edit | `adjust_position_shares()` |
| `config.py` | 📝 Edit | `MIRROR_STRICT`, `MAX_POSITIONS_PER_WALLET` |
| `main.py` | 📝 Edit | Integração do wallet monitor |
| `dashboard.py` | 📝 Edit | API + UI de copytrading |
| `COPYTRADING-MIRROR.md` | ✨ Novo | Doc completa da arquitetura (12KB) |
| `README-COPYTRADING.md` | ✨ Novo | Guia de operação (6KB) |

---

## Configuração Atual

```bash
# ~/.openclaw/workspace/penny-bot/.env (ou config.py)
PENNY_BOT_WALLET_ADDRESS=0xa445c59c0531d28a13550f29d734b33520530286
MIRROR_STRICT=true              # replica automaticamente
WALLET_MONITOR_INTERVAL_SECONDS=60
MAX_POSITIONS_PER_WALLET=200
BOT_MODE=paper
```

---

## Como Testar

### 1. Rodar o Bot

```bash
cd ~/Downloads/penny-bot
python3 main.py
```

### 2. Rodar o Dashboard (opcional)

```bash
# Terminal separado
python3 dashboard.py
# → http://127.0.0.1:5000
```

### 3. Simular Copytrading (Teste Manual)

```python
# Teste de importação
cd ~/Downloads/penny-bot
python3 -c "
from wallet_monitor import WalletMonitor
from gamma_client import get_wallet_positions
from state import StateManager

# Consultar wallet alvo
wallet = '0xa445c59c0531d28a13550f29d734b33520530286'
positions = get_wallet_positions(wallet)
print(f'Wallet tem {len(positions)} posições abertas')

for p in positions[:5]:
    print(f'  - {p.side} {p.market_id[:20]} × {p.shares} @ ${p.avg_price:.4f}')
"
```

### 4. Ver Logs de Copytrading

```bash
tail -f ~/Downloads/penny-bot/logs/*.log | grep -E "MIRROR (ENTRY|EXIT|ADJUST)|SLIPPAGE"
```

### 5. Query SQLite

```bash
sqlite3 ~/Downloads/penny-bot/data/positions.db \
  "SELECT id, market_id, side, shares, entry_price, strategy, exit_reason \
   FROM positions WHERE strategy='mirror_copy' LIMIT 10;"
```

---

## Próximos Passos (Opcionais)

- [ ] **Telegram integration**: enviar eventos como mensagens
- [ ] **Capturar wallet exit price real**: do polling da wallet (hoje usa preço de mercado)
- [ ] **Multi-wallet**: poller múltiplas carteiras (ex: penny + BTC micro)
- [ ] **Reduzir polling**: 60s → 30s se slippage consistently <1%
- [ ] **Backtesting**: performance histórica do copytrading

---

## Riscos e Mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Slippage alto (>5%) | PnL menor que wallet | Alerta emitido; reduzir polling interval |
| Wallet com muitas posições | Teto atingido (200) | `MAX_POSITIONS_PER_WALLET` configurável |
| API Polymarket instável | Copytrading falha | Retry automático; logs de erro |
| Divergência wallet vs bot | Exposição indesejada | Snapshot automático; auditoria via SQLite |

---

## Critérios de Sucesso

✅ **Entradas**: Wallet abre → bot abre (mesmos parâmetros)  
✅ **Saídas**: Wallet fecha → bot fecha (≤60s)  
✅ **Ajustes**: Wallet ajusta shares → bot ajusta (≤60s)  
✅ **Slippage**: Monitorado e alertado se >5%  
✅ **Auditoria**: Logs + snapshots + SQLite  
✅ **Dashboard**: UI em tempo real com eventos de copytrading

---

## Referências

- **Doc completa:** `COPYTRADING-MIRROR.md`
- **Guia de operação:** `README-COPYTRADING.md`
- **Dashboard:** `http://127.0.0.1:5000`
- **Wallet alvo:** `0xa445c59c0531d28a13550f29d734b33520530286` (penny strategy)

---

**Implementado em:** 2026-04-17  
**Status:** ✅ Pronto para produção (modo paper)  
**Próxima review:** Após 24h de operação contínua
