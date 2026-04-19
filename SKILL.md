---
name: polymarket-bot
description: Gerencia o Polymarket Probability Bot em paper trading local. Use esta skill quando o usuário pedir para iniciar, rodar, monitorar, parar, ver relatório, ver status, exportar dados ou fazer qualquer operação no polymarket bot, penny bot, bot de predição, ou bot de paper trading da Polymarket. Também ativa para comandos como "rodar o bot", "ver PnL", "iniciar scan", "fazer digest", "exportar trades" ou qualquer combinação dessas palavras.
---

# Polymarket Probability Bot — Skill

## Sobre o projeto

Bot de paper trading para a Polymarket. Duas estratégias:
- **Penny** — compra YES tokens ≤ 4¢ com payoff assimétrico (paga $1 se resolver YES)
- **NO Sistemático** — compra NO tokens ≤ 50¢ com win rate estimada em 70%

Modo paper trading por padrão — nenhuma ordem real é enviada. Tudo registrado em SQLite local.

## Localização do projeto

O projeto fica em `~/polymarket-probability-bot/` (confirme com o usuário se o caminho for diferente).

Antes de qualquer comando, verifique se o diretório existe:

```bash
ls ~/polymarket-probability-bot/run.py
```

Se não existir, informe o usuário e peça o caminho correto.

## Pré-requisitos

Verifique se o Python e o requests estão disponíveis antes de rodar qualquer comando:

```bash
python3 --version
python3 -c "import requests; print('requests OK')"
```

Se `requests` não estiver instalado:

```bash
pip install requests --break-system-packages
```

## Comandos disponíveis

Todos os comandos são rodados dentro do diretório do projeto:

```bash
cd ~/polymarket-probability-bot && python run.py <comando>
```

| Comando | O que faz |
|---|---|
| `scan` | Busca mercados, filtra e abre novas posições |
| `monitor` | Verifica preços e fecha posições em TP/SL/resolução |
| `status` | Resumo rápido: posições abertas, PnL, win rate |
| `report` | Relatório detalhado por estratégia e categoria |
| `digest` | Gera e envia daily digest (também imprime no terminal) |
| `export` | Exporta CSV + JSON do log completo de trades |
| `loop` | Ciclo contínuo: scan (1h) + monitor (5min) + digest (diário) |

Opções adicionais:
- `--verbose` / `-v` — logs detalhados
- `--bankroll 5000` — bankroll em dólares para sizing (padrão: $1000)

## Fluxo recomendado para iniciar

Quando o usuário quiser "começar" ou "iniciar o bot", siga esta sequência:

### 1. Verificar pré-requisitos
```bash
cd ~/polymarket-probability-bot
python3 -c "import requests" && echo "OK" || echo "FALTANDO"
```

### 2. Primeiro scan (para popular o banco com posições)
```bash
cd ~/polymarket-probability-bot && python run.py scan --verbose
```

Aguarde o scan terminar. Ele vai mostrar quantos mercados encontrou e quantas posições abriu.

### 3. Verificar resultado
```bash
cd ~/polymarket-probability-bot && python run.py status
```

### 4. Iniciar o loop contínuo (em background)
```bash
cd ~/polymarket-probability-bot && nohup python run.py loop > logs/bot.log 2>&1 &
echo "Bot rodando com PID: $!"
```

Crie o diretório de logs antes se não existir:
```bash
mkdir -p ~/polymarket-probability-bot/logs
```

## Como parar o loop

```bash
# Ver o PID do processo rodando
ps aux | grep "run.py loop" | grep -v grep

# Parar graciosamente (espera o ciclo atual terminar)
kill -TERM <PID>
```

## Ver logs em tempo real

```bash
tail -f ~/polymarket-probability-bot/logs/bot.log
```

## Verificar posições abertas (consulta direta no SQLite)

```bash
cd ~/polymarket-probability-bot
sqlite3 data/positions.db "SELECT market_id, strategy, side, entry_price, shares, cost FROM positions WHERE status='open' ORDER BY opened_at DESC LIMIT 20;"
```

## Ver PnL por estratégia

```bash
cd ~/polymarket-probability-bot
sqlite3 data/positions.db "SELECT strategy, COUNT(*) as trades, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, ROUND(SUM(pnl),2) as total_pnl FROM positions WHERE status IN ('closed','resolved') GROUP BY strategy;"
```

## Exportar dados para análise

```bash
cd ~/polymarket-probability-bot && python run.py export
```

Os arquivos são salvos em `data/exports/`:
- `trade_log_YYYYMMDD.csv` — abre no Excel/Google Sheets
- `trade_log_YYYYMMDD.json` — para scripts
- `report_YYYYMMDD.json` — métricas estruturadas

## Configurar Telegram (opcional)

Se o usuário quiser alertas no Telegram:

```bash
export TELEGRAM_TOKEN="token_do_botfather"
export TELEGRAM_CHAT_ID="seu_chat_id"
cd ~/polymarket-probability-bot && python run.py digest  # testar envio
```

Para tornar permanente, adicionar ao `~/.bashrc` ou `~/.zshrc`:
```bash
echo 'export TELEGRAM_TOKEN="token_aqui"' >> ~/.bashrc
echo 'export TELEGRAM_CHAT_ID="chat_id_aqui"' >> ~/.bashrc
source ~/.bashrc
```

## Configurar categorias

Para mudar as áreas de apostas, editar `config.py`:

```python
ALLOWED_CATEGORIES = (
    "crypto",
    "sports",
    "tech",
    "finance",
    # "politics",       # descomente para ativar
    # "entertainment",  # descomente para ativar
    # "geopolitics",    # descomente para ativar
)
```

Após editar, o próximo scan usa as novas categorias automaticamente.

## Estrutura de arquivos importante

```
~/polymarket-probability-bot/
├── run.py              → Ponto de entrada (use este)
├── config.py           → Configurações (categorias, estratégias)
├── data/
│   ├── positions.db    → Banco SQLite com todas as posições
│   ├── snapshots/      → Backups JSON diários
│   └── exports/        → CSVs e JSONs exportados
└── logs/
    └── bot.log         → Log do loop contínuo
```

## Erros comuns e soluções

**ModuleNotFoundError: requests**
```bash
pip install requests --break-system-packages
```

**`data/positions.db` não existe**
O banco é criado automaticamente no primeiro `scan`. Só rode `python run.py scan`.

**Bot parou de atualizar preços**
O mercado pode estar sem liquidez. Verifique o log:
```bash
tail -50 ~/polymarket-probability-bot/logs/bot.log | grep -i erro
```

**Quer reiniciar do zero**
```bash
rm ~/polymarket-probability-bot/data/positions.db
python run.py scan
```
Atenção: isso apaga todo o histórico.

## Resposta ao usuário

Após cada operação, sempre mostre:
1. O output do comando rodado
2. Um resumo do que aconteceu (quantas posições, PnL, etc.)
3. O próximo passo sugerido (ex: "Quer iniciar o loop contínuo?")

Mantenha o tom direto e objetivo. Não explique o funcionamento interno do bot a menos que o usuário pergunte.
