# Polymarket Probability Bot

Bot de paper trading para a [Polymarket](https://polymarket.com). Monitora mercados de predição, calcula valor esperado e gerencia posições automaticamente.

**Paper trading por padrão** — opera com dinheiro simulado.

---

## As duas estratégias

### Penny (YES ≤ 4¢)
Compra o lado YES de mercados onde o preço está abaixo de 4 centavos. A lógica: a maioria vai a zero, mas os poucos que acertam pagam $1.00 — o perfil assimétrico compensa as perdas. O bot **nunca fecha no bounce** para esta estratégia, porque cortar os winners cedo destrói o EV.

### NO Sistemático (NO ≤ 50¢)
Compra o lado NO de mercados onde o NO está barato. Win rate histórica estimada em 70%. Fecha automaticamente se houver um bounce grande (≥ 50% do caminho até o take profit).

---

## Instalação

```bash
pip install requests flask --break-system-packages
```

---

## Uso

```bash
python run.py scan           # Busca mercados e abre posições
python run.py monitor        # Verifica preços e fecha posições (TP/SL)
python run.py report         # Relatório detalhado no terminal
python run.py status         # Status rápido do portfolio
python run.py digest         # Envia daily digest no Telegram
python run.py export         # Exporta CSV + JSON
python run.py loop           # Ciclo contínuo (scan 1h + monitor 5min)
python run.py loop --verbose # Modo contínuo com logs detalhados
python run.py scan -b 5000   # Scan com bankroll de $5.000

python3 dashboard.py         # Dashboard web em http://localhost:5000
```

---

## Configuração

Edite `config.py` para ajustar categorias e parâmetros das estratégias.

### Categorias

```python
ALLOWED_CATEGORIES = (
    "crypto",
    "sports",
    "tech",
    "finance",
)
```

Disponíveis: `politics`, `finance`, `crypto`, `sports`, `tech`, `entertainment`, `geopolitics`.

### Parâmetros das estratégias

| Parâmetro | Penny | NO Sist. | O que faz |
|---|---|---|---|
| `max_price` | $0.04 | $0.50 | Preço máximo para entrar |
| `min_liquidity` | $1.000 | $1.000 | Liquidez mínima do mercado |
| `min_days_to_expiry` | 14 | 14 | Mínimo de dias até vencer |
| `max_days_to_expiry` | 200 | 200 | Máximo de dias até vencer |
| `max_positions` | 100 | 100 | Posições simultâneas |
| `max_per_event` | 3 | 3 | Máximo no mesmo evento |
| `kelly_fraction` | 0.25 | 0.25 | Tamanho da posição (Quarter-Kelly) |
| `base_win_rate` | 5% | 70% | Win rate estimada |
| `take_profit` | 3x | 1.5x | Fechar quando preço atingir alvo |
| `stop_loss` | 50% | 50% | Fechar quando perder metade |
| `bounce_exit_threshold` | None | 0.5 | Fechamento em bounce |

### Telegram (opcional)

```bash
export TELEGRAM_TOKEN="seu_token"
export TELEGRAM_CHAT_ID="seu_chat_id"
```

---

## Estrutura de arquivos

```
├── config.py        → Configurações centrais
├── state.py         → Banco de dados SQLite
├── gamma_client.py  → Busca de mercados (Gamma API)
├── clob_client.py   → Preços em tempo real (CLOB API)
├── geoblock.py      → Verificação geográfica
├── scanner.py       → Varre mercados por categoria
├── filters.py       → 8 regras de elegibilidade
├── strategy.py      → Cálculo de EV e sizing
├── paper_engine.py  → Execução de ordens (paper)
├── monitor.py       → Monitoramento de preços e saídas
├── analytics.py     → Métricas e relatórios
├── telegram_bot.py  → Alertas no Telegram
├── run.py           → Interface de linha de comando
├── dashboard.py     → Dashboard web (Flask)
└── data/
    ├── positions.db → Banco principal
    ├── snapshots/   → Backups JSON diários
    └── exports/     → CSVs e JSONs exportados
```

---

## Aviso

Este bot é uma ferramenta de **paper trading** (simulação). Nenhuma ordem real é enviada à Polymarket em modo paper. **Mercados de predição envolvem risco. Use com responsabilidade.**
