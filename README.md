# Polymarket Probability Bot

Bot de paper trading para a [Polymarket](https://polymarket.com) — a maior plataforma de mercados de predição do mundo. O bot encontra oportunidades, calcula o valor esperado, gerencia posições automaticamente e te avisa no Telegram.

**Paper trading por padrão** — opera com dinheiro simulado. Você vê exatamente o que aconteceria sem arriscar nada de verdade.

---

## O que o bot faz

1. **Escaneia** a Polymarket a cada hora, buscando mercados por categoria
2. **Filtra** os mercados segundo 8 critérios (preço, liquidez, prazo, etc.)
3. **Avalia** o valor esperado de cada oportunidade e calcula o tamanho ideal da posição
4. **Executa** as entradas (simuladas) e registra tudo no banco de dados
5. **Monitora** as posições a cada 5 minutos — fecha automaticamente em take profit, stop loss ou resolução
6. **Alerta** você no Telegram em tempo real sobre entradas, saídas e movimentos relevantes
7. **Analisa** o desempenho com métricas detalhadas e exporta logs completos

---

## As duas estratégias

### Penny (YES ≤ 4¢)
Compra o lado YES de mercados onde o preço está abaixo de 4 centavos. A lógica: a maioria vai a zero, mas os poucos que acertam pagam $1.00 — o perfil assimétrico compensa as perdas. O bot **nunca fecha no bounce** para esta estratégia, porque cortar os winners cedo destrói o EV.

### NO Sistemático (NO ≤ 50¢)
Compra o lado NO de mercados onde o NO está barato. Win rate histórica estimada em 70%. Fecha automaticamente se houver um bounce grande (≥ 50% do caminho até o take profit) porque o perfil de retorno é mais simétrico.

---

## Instalação

### Requisitos
- Python 3.12+
- Linux (testado em Linux Mint, Ubuntu 24)

### Passos

```bash
# 1. Clone ou copie os arquivos do projeto
cd polymarket-probability-bot

# 2. Instale as dependências
pip install requests --break-system-packages

# 3. Configure as variáveis de ambiente
export TELEGRAM_TOKEN="seu_token_do_botfather"    # opcional
export TELEGRAM_CHAT_ID="seu_chat_id"             # opcional

# 4. Rode o primeiro scan
python run.py scan --verbose

# 5. Veja o resultado
python run.py report
```

### Configurar o Telegram (opcional mas recomendado)

1. Abra o [@BotFather](https://t.me/botfather) no Telegram
2. Envie `/newbot` e siga as instruções
3. Copie o token gerado
4. Para obter seu chat ID: envie uma mensagem ao bot e visite `https://api.telegram.org/bot{TOKEN}/getUpdates`
5. Exporte as variáveis antes de rodar o bot

---

## Uso

```bash
# Scan único — busca mercados e abre posições
python run.py scan

# Um ciclo do monitor — verifica preços e fecha posições se necessário
python run.py monitor

# Relatório completo no terminal
python run.py report

# Status rápido do portfolio
python run.py status

# Envia daily digest pro Telegram
python run.py digest

# Exporta CSV + JSON do log completo de trades
python run.py export

# Ciclo contínuo (scan 1h + monitor 5min + digest diário)
python run.py loop

# Com logs detalhados
python run.py loop --verbose

# Com bankroll personalizado (padrão: $1000)
python run.py scan --bankroll 5000
```

### Rodar via cron

Se preferir rodar comandos individuais em vez do `loop`:

```cron
# Scan a cada hora
0 * * * * cd /home/user/polymarket-probability-bot && python run.py scan

# Monitor a cada 5 minutos
*/5 * * * * cd /home/user/polymarket-probability-bot && python run.py monitor

# Digest diário às 8h
0 8 * * * cd /home/user/polymarket-probability-bot && python run.py digest
```

---

## Configuração

Todas as configurações ficam em `config.py`. Os parâmetros mais importantes:

### Categorias de mercado

```python
ALLOWED_CATEGORIES = (
    "crypto",
    "sports",
    "tech",
    "finance",
)
```

Categorias disponíveis: `politics`, `finance`, `crypto`, `sports`, `tech`, `entertainment`, `geopolitics`. Edite a lista para escolher onde o bot opera.

### Parâmetros das estratégias

| Parâmetro | Penny | NO Sist. | O que faz |
|---|---|---|---|
| `max_price` | $0.04 | $0.50 | Preço máximo para entrar |
| `min_liquidity` | $1.000 | $1.000 | Liquidez mínima do mercado |
| `min_days_to_expiry` | 14 | 14 | Mínimo de dias até vencer |
| `max_days_to_expiry` | 200 | 200 | Máximo de dias até vencer |
| `max_positions` | 100 | 100 | Posições simultâneas por estratégia |
| `max_per_event` | 3 | 3 | Máximo no mesmo evento (anti-correlação) |
| `kelly_fraction` | 0.25 | 0.25 | Tamanho da posição (Quarter-Kelly) |
| `base_win_rate` | 5% | 70% | Win rate histórica estimada |
| `take_profit` | 3x | 1.5x | Fechar quando preço triplicar/dobrar |
| `stop_loss` | 50% | 50% | Fechar quando perder metade |
| `bounce_exit_threshold` | None | 0.5 | Fechamento automático em bounce |

---

## Estrutura de arquivos

```
polymarket-probability-bot/
├── config.py           → Todas as configurações em um lugar
├── state.py            → Banco de dados (SQLite) + backups JSON
├── gamma_client.py     → Comunicação com a Gamma API (busca de mercados)
├── clob_client.py      → Comunicação com a CLOB API (preços em tempo real)
├── geoblock.py         → Verificação de restrições geográficas
├── scanner.py          → Busca mercados por categoria
├── filters.py          → 8 regras de elegibilidade
├── strategy.py         → Cálculo de EV e tamanho de posição
├── paper_engine.py     → Execução de ordens (paper ou live)
├── monitor.py          → Monitoramento de preços e condições de saída
├── analytics.py        → Métricas, relatórios e exports
├── telegram_bot.py     → Alertas no Telegram
├── run.py              → Interface de linha de comando
├── requirements.txt    → Dependências Python
└── docs/
    ├── README.md        → Este arquivo
    ├── API.md           → Referência completa das APIs da Polymarket
    └── MODULES.md       → Documentação detalhada de cada módulo
```

---

## Dados gerados

O bot salva tudo em `data/`:

```
data/
├── positions.db        → Banco principal (SQLite)
├── snapshots/          → Backups JSON diários
└── exports/            → CSVs e JSONs para análise
```

### O que fica no banco

- **Posições** — toda entrada e saída, com preços, PnL, motivo de fechamento
- **Histórico de trades** — sequência exata de cada open/close com timestamps
- **Cache de mercados** — dados dos mercados escaneados (atualizado a cada scan)

---

## Aviso

Este bot é uma ferramenta de **paper trading** (simulação). Em modo paper, nenhuma ordem real é enviada à Polymarket — os dados são apenas simulados localmente.

O modo live (com ordens reais) requer configuração adicional de carteira Polygon, chaves de API CLOB e o SDK `py-clob-client`. Veja `docs/API.md` para detalhes sobre autenticação.

**Mercados de predição envolvem risco. Use com responsabilidade.**
