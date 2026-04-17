---
name: polymarket-bot
description: Gerencia o Polymarket Probability Bot em paper trading local. Use esta skill quando o usuário pedir para iniciar, rodar, monitorar, parar, configurar, ver relatório, ver status, exportar dados, ver logs ou fazer qualquer operação no polymarket bot, penny bot, bot de predição, ou bot de paper trading da Polymarket. Também ativa para comandos como "rodar o bot", "ver PnL", "iniciar scan", "fazer digest", "exportar trades", "configurar cron", "ver crontab" ou qualquer combinação dessas palavras.
---

# Polymarket Probability Bot — Skill

## Sobre o projeto

Bot de paper trading para a Polymarket. Duas estratégias:
- **Penny** — compra YES tokens ≤ 4¢ com payoff assimétrico (paga $1 se resolver YES)
- **NO Sistemático** — compra NO tokens ≤ 50¢ com win rate estimada em 70%

Modo paper trading — nenhuma ordem real é enviada. Tudo registrado em SQLite local.

**Arquitetura de execução:** cada comando do bot (`scan`, `monitor`, `digest`, `export`) roda como um job independente no cron. Não existe processo contínuo em background — o cron é o scheduler.

## Localização do projeto

O projeto fica em `~/polymarket-probability-bot/` (confirme com o usuário se o caminho for diferente).

Antes de qualquer ação, verifique se o diretório existe:

```bash
ls ~/polymarket-probability-bot/run.py
```

Se não existir, informe o usuário e peça o caminho correto.

## Pré-requisitos

```bash
python3 --version
python3 -c "import requests; print('requests OK')" || pip install requests --break-system-packages
```

## Comandos disponíveis

Cada comando é executado diretamente e termina sozinho — sem processos em background:

```bash
cd ~/polymarket-probability-bot && python3 run.py <comando>
```

| Comando | Frequência ideal | O que faz |
|---|---|---|
| `scan` | 1× por hora | Busca mercados, filtra e abre novas posições |
| `monitor` | 1× a cada 5 min | Verifica preços e fecha posições em TP/SL/resolução |
| `digest` | 1× por dia | Gera e envia resumo do portfolio (Telegram + terminal) |
| `export` | 1× por semana | Exporta CSV + JSON do log completo de trades |
| `status` | Sob demanda | Resumo rápido: posições abertas, PnL, win rate |
| `report` | Sob demanda | Relatório detalhado por estratégia e categoria |

Opções adicionais:
- `--verbose` / `-v` — logs detalhados (útil para testar manualmente)
- `--bankroll 5000` — bankroll em dólares para sizing (padrão: $1000)

O comando `loop` existe no código mas **não deve ser usado** — o cron substitui essa função com mais confiabilidade.

## Fluxo recomendado para iniciar

### 1. Verificar pré-requisitos
```bash
cd ~/polymarket-probability-bot
python3 -c "import requests" && echo "OK" || echo "FALTANDO"
```

### 2. Criar diretório de logs
```bash
mkdir -p ~/polymarket-probability-bot/logs
```

### 3. Primeiro scan manual (para popular o banco)
```bash
cd ~/polymarket-probability-bot && python3 run.py scan --verbose
```

Aguarde terminar. O output mostra quantos mercados encontrou e quantas posições abriu.

### 4. Verificar resultado
```bash
cd ~/polymarket-probability-bot && python3 run.py status
```

### 5. Configurar o cron
```bash
crontab -e
```

Cole as linhas abaixo (ajuste o caminho e credenciais):

```cron
# Polymarket Probability Bot
# Ajuste BOT_DIR com o caminho absoluto real do projeto
BOT_DIR=/home/SEU_USUARIO/polymarket-probability-bot
TELEGRAM_TOKEN=seu_token_aqui
TELEGRAM_CHAT_ID=seu_chat_id_aqui

# Monitor: a cada 5 minutos
*/5 * * * * cd $BOT_DIR && python3 run.py monitor >> logs/monitor.log 2>&1

# Scan: a cada hora (no minuto 0)
0 * * * * cd $BOT_DIR && python3 run.py scan >> logs/scan.log 2>&1

# Digest diário: toda vez às 8h da manhã
0 8 * * * cd $BOT_DIR && python3 run.py digest >> logs/digest.log 2>&1

# Export semanal: toda segunda às 9h
0 9 * * 1 cd $BOT_DIR && python3 run.py export >> logs/export.log 2>&1
```

**Pontos de atenção:**
- Use o **caminho absoluto** em `BOT_DIR` — o cron não expande `~`
- Confirme o caminho do Python com `which python3` e use-o se for diferente de `python3`
- As variáveis `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` devem estar no crontab, não no `.bashrc` — o cron não carrega o ambiente do shell

### 6. Confirmar configuração
```bash
crontab -l
```

### 7. Testar que o cron consegue executar o bot

Simule o ambiente restrito do cron antes de confiar nele:

```bash
env -i HOME=$HOME PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  bash -c 'cd ~/polymarket-probability-bot && python3 run.py status'
```

Se funcionar aqui, funcionará no cron. Se falhar, o problema aparece agora — não depois de horas de silêncio.

## Gerenciar o cron

### Ver configuração atual
```bash
crontab -l
```

### Editar (pausar, mudar horários, adicionar jobs)
```bash
crontab -e
```

Para **pausar temporariamente** um job sem apagar, comente a linha com `#`:
```cron
# */5 * * * * cd $BOT_DIR && python3 run.py monitor >> logs/monitor.log 2>&1
```

### Remover todos os jobs do bot
```bash
crontab -l | grep -v "polymarket\|BOT_DIR\|TELEGRAM" | crontab -
```

## Ver logs

Cada job tem seu próprio arquivo de log:

```bash
# Últimas execuções do monitor
tail -50 ~/polymarket-probability-bot/logs/monitor.log

# Últimas execuções do scan
tail -50 ~/polymarket-probability-bot/logs/scan.log

# Digest mais recente
tail -30 ~/polymarket-probability-bot/logs/digest.log

# Verificar erros em todos os logs
grep -i "error\|erro\|traceback\|exception" ~/polymarket-probability-bot/logs/*.log
```

Para acompanhar o monitor enquanto roda:
```bash
watch -n 5 tail -20 ~/polymarket-probability-bot/logs/monitor.log
```

## Consultas rápidas ao banco

```bash
# Posições abertas
sqlite3 ~/polymarket-probability-bot/data/positions.db \
  "SELECT market_id, strategy, side, entry_price, shares, cost FROM positions WHERE status='open' ORDER BY opened_at DESC LIMIT 20;"

# PnL por estratégia
sqlite3 ~/polymarket-probability-bot/data/positions.db \
  "SELECT strategy, COUNT(*) trades, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins, ROUND(SUM(pnl),2) pnl FROM positions WHERE status IN ('closed','resolved') GROUP BY strategy;"

# Últimas 10 saídas
sqlite3 ~/polymarket-probability-bot/data/positions.db \
  "SELECT market_id, strategy, exit_reason, ROUND(pnl,2) pnl, closed_at FROM positions WHERE status IN ('closed','resolved') ORDER BY closed_at DESC LIMIT 10;"
```

## Exportar dados para análise

```bash
cd ~/polymarket-probability-bot && python3 run.py export
```

Arquivos gerados em `data/exports/`:
- `trade_log_YYYYMMDD.csv` — abre no Excel/Google Sheets
- `trade_log_YYYYMMDD.json` — para scripts externos
- `report_YYYYMMDD.json` — métricas estruturadas

## Configurar Telegram

As variáveis devem estar **no crontab**, não no `.bashrc` (o cron não herda o ambiente do shell):

```cron
TELEGRAM_TOKEN=seu_token_aqui
TELEGRAM_CHAT_ID=seu_chat_id_aqui
```

Para testar o envio manualmente antes de colocar no cron:
```bash
TELEGRAM_TOKEN=seu_token TELEGRAM_CHAT_ID=seu_chat_id \
  python3 ~/polymarket-probability-bot/run.py digest
```

## Configurar categorias de apostas

Editar `config.py`:

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

O próximo scan do cron usa as novas categorias automaticamente — não precisa reiniciar nada.

## Estrutura de arquivos

```
~/polymarket-probability-bot/
├── run.py              → Ponto de entrada de todos os comandos
├── config.py           → Configurações (categorias, estratégias)
├── data/
│   ├── positions.db    → Banco SQLite com todas as posições
│   ├── snapshots/      → Backups JSON (gerados pelo digest)
│   └── exports/        → CSVs e JSONs exportados
└── logs/
    ├── monitor.log     → Log do job de monitor (cron)
    ├── scan.log        → Log do job de scan (cron)
    ├── digest.log      → Log do job de digest (cron)
    └── export.log      → Log do job de export (cron)
```

## Erros comuns e soluções

**Job do cron não roda / silêncio total**
O cron não carrega o PATH do usuário. Confirme com `which python3` e use o caminho absoluto no crontab se necessário (ex: `/usr/bin/python3 run.py monitor`).

**ModuleNotFoundError: requests**
```bash
pip install requests --break-system-packages
```
Se o cron usa um Python diferente do shell: `/usr/bin/python3 -m pip install requests --break-system-packages`

**`data/positions.db` não existe**
O banco é criado automaticamente no primeiro `scan`. Rode manualmente uma vez antes do cron entrar em ação.

**Quer reiniciar o histórico do zero**
```bash
rm ~/polymarket-probability-bot/data/positions.db
python3 ~/polymarket-probability-bot/run.py scan
```
Atenção: apaga todo o histórico de trades.

## Resposta ao usuário

Após cada operação, sempre mostre:
1. O output do comando rodado (ou o conteúdo relevante do log)
2. Um resumo do que aconteceu (posições abertas, PnL, status dos jobs do cron)
3. O próximo passo sugerido

Mantenha o tom direto. Não explique o funcionamento interno a menos que o usuário pergunte.
