# Guia de Início Rápido

Do zero ao primeiro scan em 5 minutos.

---

## Passo 1 — Instalar dependências

```bash
pip install requests --break-system-packages
```

Só isso. O bot usa apenas `requests` para comunicação HTTP. O SQLite já vem no Python.

---

## Passo 2 — Configurar categorias (opcional)

Abra `config.py` e edite:

```python
ALLOWED_CATEGORIES = (
    "crypto",    # Bitcoin, Ethereum, etc.
    "sports",    # NBA, NFL, futebol, etc.
    "tech",      # Lançamentos de produtos, Apple, Google, etc.
    "finance",   # Fed, taxas de juros, mercado financeiro
)
```

Remova as que não quer. Adicione outras disponíveis: `politics`, `entertainment`, `geopolitics`.

---

## Passo 3 — Configurar Telegram (opcional)

Sem Telegram, o bot funciona normalmente — você só não recebe alertas.

Para configurar:

1. Abra [@BotFather](https://t.me/botfather) no Telegram
2. Envie `/newbot`
3. Siga as instruções e copie o token
4. Para obter seu chat ID: envie qualquer mensagem ao seu bot novo, depois acesse:
   ```
   https://api.telegram.org/bot{SEU_TOKEN}/getUpdates
   ```
   O chat ID aparece no campo `"id"` dentro de `"chat"`.

5. Exporte as variáveis:

```bash
export TELEGRAM_TOKEN="1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ"
export TELEGRAM_CHAT_ID="987654321"
```

---

## Passo 4 — Primeiro scan

```bash
python run.py scan --verbose
```

Você vai ver algo como:

```
2026-04-17 09:00:00 INFO  run: === SCAN START ===
2026-04-17 09:00:00 INFO  run: Mode: paper | Categories: ['crypto', 'sports', 'tech', 'finance']
2026-04-17 09:00:02 INFO  scanner: Escaneando categoria 'crypto' (tag_id=21)
2026-04-17 09:00:03 INFO  scanner:   → 312 mercados de crypto
2026-04-17 09:00:04 INFO  scanner: Escaneando categoria 'sports' (tag_id=100639)
...
2026-04-17 09:00:08 INFO  run: --- Estratégia: penny ---
2026-04-17 09:00:08 INFO  run:   Filtro: 23 elegíveis, 289 rejeitados (de 312 mercados)
2026-04-17 09:00:08 INFO  run:   Sinais com EV > 0: 23 (slots=100)
2026-04-17 09:00:08 INFO  run:   Entradas executadas: 23
...
2026-04-17 09:00:09 INFO  run: === SCAN END: 31 novas posições ===
```

---

## Passo 5 — Ver resultado

```bash
python run.py report
```

```
============================================================
  POLYMARKET PROBABILITY BOT — REPORT
============================================================

  Mode: paper
  Generated: 2026-04-17T09:00:10+00:00

  --- Overall ---
  Positions: 31 open, 0 closed
  Win rate:  0.0% (0W / 0L)
  PnL:       $+0.00
  Invested:  $94.23
  ROI:       0.0%
  ...
```

---

## Passo 6 — Rodar em loop contínuo

```bash
python run.py loop
```

O bot vai:
- Escanear imediatamente e depois a cada 1 hora
- Checar preços a cada 5 minutos
- Enviar daily digest no Telegram ao virar o dia UTC
- Salvar snapshot diário automaticamente

Para parar: `Ctrl+C` (espera o ciclo atual terminar e para limpo).

---

## Rodando em segundo plano

Para manter rodando mesmo após fechar o terminal:

```bash
# Com nohup
nohup python run.py loop > logs/bot.log 2>&1 &

# Com screen
screen -S polymarket-bot
python run.py loop
# Ctrl+A, D para desanexar

# Com systemd (Linux)
# Crie /etc/systemd/system/polymarket-bot.service
```

---

## Verificando o histórico

```bash
# Status rápido
python run.py status

# Relatório completo
python run.py report

# Exportar para Excel
python run.py export
# Abre data/exports/trade_log_YYYYMMDD.csv no Excel
```

---

## Comandos de referência rápida

| Comando | O que faz |
|---|---|
| `python run.py scan` | Busca mercados e abre posições |
| `python run.py monitor` | Verifica TP/SL das posições abertas |
| `python run.py status` | 6 linhas: posições, PnL, win rate |
| `python run.py report` | Relatório detalhado no terminal |
| `python run.py digest` | Envia resumo no Telegram |
| `python run.py export` | Exporta CSV + JSON |
| `python run.py loop` | Tudo automático (modo contínuo) |
| `python run.py loop -v` | Modo contínuo com logs detalhados |
| `python run.py scan -b 5000` | Scan com bankroll de $5.000 |
