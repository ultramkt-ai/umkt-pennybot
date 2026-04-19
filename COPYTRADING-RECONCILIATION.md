# Copytrading: Reconciliação e Fechamento

## Visão Geral

O sistema de copytrading espelho replica **exatamente** a carteira alvo, tanto nas entradas quanto nas saídas.

**Modo de operação:**
- `MIRROR_STRICT=True` (default): replica tudo automaticamente
- `MIRROR_STRICT=False`: só alerta, não executa (review mode)

## Problema que Resolve

Sem copytrading espelho:
- ✅ Abertura: automática (scanner → strategy → execution)
- ❌ Fechamento: manual ou perdido (wallet fecha, bot não sabe)
- ❌ Divergências: shares diferentes, exits não sincronizados
- ❌ PnL incorreto: ledger local não reflete realidade

Com copytrading espelho:
- ✅ Abertura: automática (wallet abre → bot abre)
- ✅ Fechamento: automático (wallet fecha → bot fecha)
- ✅ Ajustes: automáticos (wallet aumenta/reduz → bot ajusta)
- ✅ Slippage tracking: compara preço wallet vs bot
- ✅ PnL correto: ledger local espelha wallet

## Arquitetura

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│ Wallet Origem   │      │ Wallet Monitor   │      │ Ledger Local    │
│ (Polymarket)    │      │ (Mirror Trading) │      │ (SQLite)        │
│                 │      │                  │      │                 │
│ [POS: ABC YES]  │ ───► │ Poll (60s)       │ ───► │ [POS: ABC YES]  │
│ [POS: XYZ NO ]  │      │ Mirror Actions   │      │ [POS: XYZ NO ]  │
│                 │      │                  │      │                 │
└─────────────────┘      └──────────────────┘      └─────────────────┘
                                │
                                ▼
                         [Ações Automáticas]
                         - wallet abriu → abre local (mesmos shares)
                         - wallet fechou → fecha local
                         - wallet aumentou → aumenta local
                         - wallet reduziu → reduz local
                         - slippage check → alerta se >5%
```

## Componentes

### 1. `wallet_monitor.py`

**Responsabilidades:**
- Poller da carteira origem (Polymarket API)
- **Mirror trading**: replica exatamente a wallet
  - Wallet abriu → abre no bot (mesmos shares, side, market)
  - Wallet fechou → fecha no bot
  - Wallet aumentou → aumenta no bot (delta shares)
  - Wallet reduziu → reduz no bot (partial exit)
- Slippage tracking: compara preço de saída wallet vs bot
- Emitir eventos para auditoria

**Ciclo (60 segundos):**
1. Carrega posições abertas da wallet (`GET /positions?user={wallet}`)
2. Carrega posições abertas do ledger local (SQLite)
3. Compara por `(market_id, side)`
4. Executa ações automáticas:
   - **Nova posição na wallet** → abre no bot com mesmos parâmetros
   - **Posição fechada na wallet** → executa exit local com `reason="wallet_exit"`
   - **Wallet aumentou shares** → compra delta shares no bot
   - **Wallet reduziu shares** → vende delta shares (partial exit)
   - **Slippage >5%** → alerta de slippage

**Exemplo de evento:**
```python
CopytradeEvent(
    event_type="mirror_entry",
    position_id=789,
    market_id="abc123",
    question="Will BTC hit $100k in 2026?",
    details={
        "side": "YES",
        "shares": 1000,
        "entry_price": 0.03,
        "total_cost": 30.0,
        "wallet_address": "0xa445c...",
    },
    severity="info"
)
```

### 2. `gamma_client.py` (extensão)

**Nova função:**
```python
def get_wallet_positions(wallet_address: str) -> list[WalletPosition]:
    """
    GET /positions?user={wallet_address}&market_status=active
    
    Retorna lista de WalletPosition com:
      - market_id, side, shares, avg_price
      - total_cost, current_value, realized_pnl
      - token_id (para consultar preço no CLOB)
    """
```

### 3. `main.py` (orquestrador)

**Threads:**
- **Main**: scanner (1h) + strategy (contínuo)
- **Thread 1**: monitor de preços (5min) → TP/SL/resolução
- **Thread 2**: wallet monitor (1min) → reconciliação copytrading

**Configuração:**
```python
WALLET_ADDRESS = "0xa445c59c0531d28a13550f29d734b33520530286"
WALLET_MONITOR_INTERVAL_SECONDS = 60
AUTO_COPY_NEW_POSITIONS = False  # só reconcilia fechamentos, não abre novas
```

## Fluxos

### 1. Mirror Entry (Wallet Abre Posição)

**Cenário:** Wallet origem abre posição que bot ainda não tem.

```
T0: Wallet não tem [NEW YES]
    Bot não tem [NEW YES]

T1: Wallet abre [NEW YES × 500 @ $0.20]

T2: Wallet monitor poll (60s)
    - GET /positions?user=0xa445c...
    - Wallet: [NEW YES × 500 @ $0.20]
    - Bot: não tem [NEW YES]
    - Nova posição detectada

T3: Bot executa (MIRROR_STRICT=True):
    - state.open_position(
        market_id="new123",
        side="YES",
        shares=500,
        entry_price=0.20,
        strategy="mirror_copy",
        category="mirror"
      )
    - position_id=789 criado

T4: Evento emitido + snapshot salvo:
    {
        "event_type": "mirror_entry",
        "position_id": 789,
        "shares": 500,
        "entry_price": 0.20,
        "total_cost": 100.0
    }
```

**Resultado:** Bot replicou entrada exata da wallet.

### 2. Mirror Exit (Wallet Fecha Posição)

**Cenário:** Wallet reduz posição (ex: vende 50% das shares).

### 2. Mirror Exit (Wallet Fecha Posição)

**Cenário:** Wallet origem fecha posição que bot copiou.

```
T0: Wallet tem [ABC YES × 1000 @ $0.03]
    Bot tem [ABC YES × 1000 @ $0.03] (posição_id=123)

T1: Wallet fecha [ABC YES] → Polymarket executa venda
    Wallet exit_price = $0.08 (preço real da venda)

T2: Wallet monitor poll (60s)
    - GET /positions?user=0xa445c...
    - [ABC YES] sumiu da wallet
    - [ABC YES] ainda está no bot → MIRROR EXIT DETECTADO

T3: Bot executa:
    - get_midpoint(token_id) → $0.08 (preço atual de mercado)
    - engine.execute_exit(position_id=123, exit_price=0.08, reason="wallet_exit")
    - SQLite atualiza: status="closed", pnl=+50.0
    - Slippage check: wallet=$0.08, bot=$0.08 → slippage=0% (OK)

T4: Evento emitido + snapshot salvo:
    {
        "event_type": "mirror_exit",
        "position_id": 123,
        "exit_price": 0.08,
        "pnl": +50.0,
        "wallet_exit_price": 0.08,
        "slippage": 0.0,
        "slippage_pct": 0.0
    }
```

**Resultado:** Bot saiu junto com a wallet, slippage monitorado, PnL registrado.

### 3. Mirror Adjust (Wallet Ajusta Shares)

**Cenário:** Wallet aumenta ou reduz posição existente.

```
T0: Wallet tem [XYZ NO × 2000 @ $0.30]
    Bot tem [XYZ NO × 2000 @ $0.30] (posição_id=456)

T1: Wallet vende 1000 shares → fica com 1000 shares
    Wallet reduz de 2000 → 1000 (-50%)

T2: Wallet monitor poll (60s)
    - Wallet: [XYZ NO × 1000]
    - Bot: [XYZ NO × 2000]
    - Divergência detectada: -1000 shares

T3: Bot executa (MIRROR_STRICT=True):
    - get_midpoint(token_id) → $0.35 (preço atual)
    - state.adjust_position_shares(456, delta_shares=-1000, adjustment_price=0.35)
    - SQLite atualiza: shares=1000, pnl_realizado=+$50.00
    - Nova posição: 1000 shares @ $0.30 (preço médio mantido)

T4: Evento emitido:
    {
        "event_type": "mirror_adjust",
        "position_id": 456,
        "delta_shares": -1000,
        "adjustment_price": 0.35,
        "adjustment_type": "decrease",
        "partial_pnl": +50.00
    }
```

**Resultado:** Bot replicou ajuste exato da wallet, PnL parcial realizado.

### 4. Slippage Alert

**Cenário:** Bot sai com preço diferente da wallet (lag ou liquidez).

### Variáveis de Ambiente

```bash
# Wallet de copytrading para reconciliação
PENNY_BOT_WALLET_ADDRESS=0xa445c59c0531d28a13550f29d734b33520530286

# Modo de operação
BOT_MODE=paper  # ou "live"

# Intervalo de reconciliação (segundos)
WALLET_MONITOR_INTERVAL_SECONDS=60

# Auto-copy de novas posições (default: False)
AUTO_COPY_NEW_POSITIONS=False
```

### `config.py`

```python
WALLET_ADDRESS = os.environ.get(
    "PENNY_BOT_WALLET_ADDRESS",
    "0xa445c59c0531d28a13550f29d734b33520530286"
)
WALLET_MONITOR_INTERVAL_SECONDS = 60
AUTO_COPY_NEW_POSITIONS = False
```

## Auditoria e Logs

### Logs (stdout)

```
2026-04-16 23:15:42 [INFO] wallet_monitor: Wallet 0xa445c... tem 3 posições abertas
2026-04-16 23:15:42 [INFO] wallet_monitor: WALLET EXIT reconciliado: pos=123 YES @ $0.03 → $0.08 | PnL=+$50.00
2026-04-16 23:15:42 [INFO] wallet_monitor: DIVERGÊNCIA: NO xyz123 — wallet 1000 shares, local 2000 shares (wallet reduziu 1000 shares)
2026-04-16 23:15:42 [INFO] wallet_monitor: Reconciliação: 3 na wallet, 0 novas, 1 exits, 1 parciais, 1 divergências, 0 erros
```

### JSON Snapshots

Sempre que há exits reconciliados, um snapshot JSON é salvo:

```bash
data/snapshots/snapshot_20260416_231542.json
```

Contém:
- `positions`: estado completo do SQLite
- `trades_history`: log de todas as operações (open + close)
- `market_cache`: mercados escaneados

### Trade Log (CSV/JSON)

Analytics exporta log completo:

```bash
data/exports/trade_log_20260416_231542.csv
data/exports/trade_log_20260416_231542.json
```

Campos chave:
- `position_id`, `market_id`, `strategy`, `side`
- `entry_price`, `exit_price`, `shares`, `pnl`
- `exit_reason` (inclui "wallet_exit")
- `opened_at`, `closed_at`, `hold_hours`

## Reason Codes

O campo `exit_reason` identifica como a posição foi fechada:

| Reason | Descrição | Quem aciona |
|--------|-----------|-------------|
| `take_profit` | Preço atingiu TP | Monitor (preço) |
| `stop_loss` | Preço atingiu SL | Monitor (preço) |
| `resolved_win` | Mercado resolveu e ganhou | Monitor (resolução) |
| `resolved_loss` | Mercado resolveu e perdeu | Monitor (resolução) |
| `wallet_exit` | Wallet origem fechou | Wallet Monitor |
| `partial_exit` | Wallet reduziu posição | Wallet Monitor (alerta) |
| `bounce_exit` | Bounce significativo (NO strat) | Monitor (bounce) |
| `manual` | Fechamento manual | Usuário (futuro) |
| `expired` | Mercado expirou sem resolução | Monitor (expiry) |

## Slippage Tracking (TODO)

Hoje o slippage não é calculado. Futuro:

```python
# No wallet_monitor._handle_wallet_exit():
wallet_exit_price = wp.exit_price  # viria do polling da wallet
local_exit_price = exit_price      # preço que bot conseguiu

slippage = (local_exit_price - wallet_exit_price) / wallet_exit_price
```

Isso permite medir:
- Quanto o bot perdeu/ganhou vs wallet no timing de saída
- Se o polling de 60s está bom ou precisa ser mais frequente
- Impacto de liquidez no fill

## Limitações Atuais

1. **Auto-copy de novas posições**: só emite alerta, não abre automaticamente
2. **Partial exit**: só alerta, não reconcilia automaticamente
3. **Slippage**: não calculado (falta capturar preço real da wallet)
4. **Fechamentos em lote**: wallet pode fechar 10 posições de uma vez → bot fecha uma por uma (OK, mas pode ser otimizado)
5. **Rate limits**: polling de 60s é conservador; API Polymarket aceita mais frequência

## Próximos Passos

- [ ] Implementar `AUTO_COPY_NEW_POSITIONS=True` (abre posições novas automaticamente)
- [ ] Implementar reconciliação parcial (vender mesma % que wallet)
- [ ] Capturar `wallet_exit_price` real para cálculo de slippage
- [ ] Telegram integration: enviar eventos de reconciliação como mensagens
- [ ] Dashboard: aba "Copytrading" com wallet vs local, divergências, slippage
- [ ] Multi-wallet: reconciliar múltiplas carteiras origem (ex: penny + BTC micro)

## Referências

- Polymarket Positions API: https://docs.polymarket.com/api-reference/data/get-user-positions
- Gamma API: https://docs.polymarket.com/api-reference/data/get-events
- CLOB Midpoint: https://docs.polymarket.com/api-reference/data/get-midpoint-price
