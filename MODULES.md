# Documentação dos Módulos

Guia detalhado de cada arquivo do projeto — o que faz, por que existe, como usar e o que configurar.

---

## `config.py` — Painel de Controle

**O arquivo que você vai editar com mais frequência.**

Todas as configurações do bot vivem aqui: categorias onde opera, parâmetros das estratégias, tokens do Telegram, endereço do banco de dados. Nenhum outro módulo tem valores fixos no código — tudo lê daqui.

### Por que centralizar tudo aqui?

Porque quando você quiser mudar algo — aumentar o take profit, adicionar a categoria "politics", mudar o mínimo de liquidez — você sabe exatamente onde ir. Sem caçar valores espalhados em 10 arquivos.

### O que configurar primeiro

**1. Categorias de mercado** — as áreas onde o bot vai procurar oportunidades:

```python
ALLOWED_CATEGORIES = (
    "crypto",
    "sports",
    "tech",
    "finance",
)
```

Opções disponíveis: `politics`, `finance`, `crypto`, `sports`, `tech`, `entertainment`, `geopolitics`. Remova as que não quer, adicione as que quiser.

**2. Variáveis de ambiente** — configure antes de rodar:

```bash
export TELEGRAM_TOKEN="seu_token"
export TELEGRAM_CHAT_ID="seu_chat_id"
```

Opcional: o bot funciona sem o Telegram, só não vai te avisar de nada.

**3. Bankroll** — ao rodar `python run.py scan --bankroll 5000`, você define quanto dinheiro simulado o bot usa para calcular o tamanho das posições.

### Parâmetros das estratégias

Cada estratégia é definida por um conjunto de parâmetros imutáveis. Aqui o que cada um significa na prática:

| Parâmetro | Significado prático |
|---|---|
| `max_price` | "Só entro se o token custar menos que X centavos" |
| `min_liquidity` | "O mercado precisa ter pelo menos $X em liquidez" |
| `min_days_to_expiry` | "Não entro em mercado que vence em menos de X dias" |
| `max_days_to_expiry` | "Não entro em mercado que vence em mais de X dias" |
| `max_positions` | "Máximo de apostas abertas ao mesmo tempo" |
| `max_per_event` | "Máximo de apostas no mesmo evento (para não concentrar risco)" |
| `kelly_fraction` | "Usa X% do Kelly completo para dimensionar a posição" |
| `base_win_rate` | "Estimo que ganho em X% dos casos historicamente" |
| `take_profit` | "Fecha se o preço chegar a X vezes o que paguei" |
| `stop_loss` | "Fecha se perder X% do valor investido" |
| `bounce_exit_threshold` | "Para NO sist.: fecha no bounce se chegar a X% do caminho até o TP. Para penny: None (nunca fecha no bounce)" |

---

## `state.py` — O Banco de Dados

**A memória do bot. Tudo que acontece é gravado aqui.**

Usa SQLite — um banco de dados em arquivo, sem servidor, sem configuração. O arquivo `data/positions.db` é criado automaticamente na primeira vez que o bot roda.

### O que fica guardado

**Tabela `positions`** — cada aposta do bot:
- Qual mercado, qual estratégia, lado (YES ou NO)
- Preço de entrada, quantidade de shares, custo total
- Preço atual, preço alvo (TP) e stop loss
- Preço de saída, motivo do fechamento, PnL
- Timestamps de abertura e fechamento

**Tabela `trades_history`** — o log de cada ação:
- Cada abertura e fechamento tem sua linha
- Timestamps precisos para reconstruir o histórico completo

**Tabela `market_cache`** — informações dos mercados escaneados:
- Preços YES/NO, liquidez, volume, data de expiração
- Token IDs para consultar preços na CLOB API
- Status de resolução

### Backups automáticos

O bot salva um snapshot JSON completo em `data/snapshots/` uma vez por dia (ao rodar em loop). Esse arquivo contém tudo do banco em formato JSON — útil para integrar com outras ferramentas ou para recuperação em caso de problema.

### Como consultar manualmente

Se quiser explorar os dados diretamente:

```bash
sqlite3 data/positions.db

# Ver posições abertas
SELECT market_id, strategy, side, entry_price, shares, cost
FROM positions WHERE status = 'open';

# Ver PnL total por estratégia
SELECT strategy, SUM(pnl) as total_pnl, COUNT(*) as trades
FROM positions WHERE status IN ('closed','resolved')
GROUP BY strategy;
```

---

## `gamma_client.py` — Buscador de Mercados

**Fala com a API pública da Polymarket para trazer dados de mercados.**

A Gamma API é o ponto de entrada da Polymarket para quem quer explorar mercados. Ela é pública — não precisa de autenticação, não precisa de conta. O bot chama ela a cada hora para descobrir oportunidades novas.

### O que ele faz

1. **Busca eventos por categoria** — usa o `tag_id` oficial da Polymarket (por exemplo, `tag_id=21` para Crypto) para trazer só o que interessa
2. **Pagina automaticamente** — se uma categoria tem 300 mercados, ele busca em páginas de 100 até pegar todos
3. **Normaliza os dados** — a API retorna alguns campos como string JSON (ex: `outcomePrices = '["0.03","0.97"]'`). O módulo converte tudo para os tipos corretos
4. **Resiste a falhas** — se a API falhar, tenta de novo com espera exponencial (1s, 2s, 4s)

### Um detalhe técnico importante

A API retorna os preços como **strings**, não números:

```json
"outcomePrices": "[\"0.03\",\"0.97\"]"
```

Não como seria esperado:

```json
"outcomePrices": [0.03, 0.97]
```

O `gamma_client` cuida dessa conversão automaticamente.

### Rate limits respeitados

A Polymarket limita `/events` a 500 requests por 10 segundos. O bot faz no máximo 20 requests por categoria por scan — está muito dentro do limite.

---

## `clob_client.py` — Preços em Tempo Real

**Busca o preço atual de cada posição aberta durante o monitoramento.**

A CLOB API é onde ficam os preços ao vivo. Para cada posição aberta, o bot precisa saber o preço atual para decidir se é hora de fechar.

### Batch eficiente

Em vez de fazer uma request por posição (o que seria lento com 100+ posições), o bot usa o endpoint `/midpoints` que aceita múltiplos tokens de uma vez:

```
POST /midpoints
[{"token_id": "0xabc"}, {"token_id": "0xdef"}, ...]
→ {"0xabc": "0.05", "0xdef": "0.30"}
```

Com 200 posições abertas, são apenas **1 request** por ciclo de monitor — em vez de 200.

### O que é "midpoint"

O preço midpoint é a média entre o melhor preço de compra (bid) e o melhor preço de venda (ask) no livro de ordens. É o preço mais justo disponível para avaliar uma posição.

### Sem autenticação

Consultar preços é público — não precisa de conta ou chave de API.

---

## `geoblock.py` — Verificação Geográfica

**Verifica se o seu IP pode operar na Polymarket.**

A Polymarket bloqueia países específicos por regulação. O Brasil **não está bloqueado** — mas o módulo existe para verificar isso antes de tentar operar em modo live.

### Como usar

```python
from geoblock import check_geoblock

geo = check_geoblock()
if geo.blocked:
    print(f"Bloqueado em {geo.country}")
else:
    print("Pode operar normalmente")
```

### Países bloqueados

Totalmente bloqueados: EUA, Alemanha, França, Reino Unido, Itália, Holanda, Rússia, Austrália, Bélgica e outros.

Close-only (pode fechar posições existentes, mas não abrir novas): Polônia, Singapura, Tailândia, Taiwan.

Em modo paper, o geoblock não importa — não tem ordens reais sendo enviadas.

---

## `scanner.py` — Descobridor de Oportunidades

**Varre a Polymarket em busca de mercados, organiza e salva no banco.**

O scanner é quem aciona o `gamma_client` e processa os resultados. Ele roda uma vez por hora.

### Como funciona

Para cada categoria na `ALLOWED_CATEGORIES`:
1. Chama a Gamma API filtrando por `tag_id`
2. Para cada evento retornado, extrai os mercados dentro
3. Classifica cada mercado pela categoria (usando as tags oficiais)
4. Salva no `market_cache` do banco (upsert — atualiza se já existe)

### Deduplicação automática

Um mesmo mercado pode aparecer em múltiplas categorias (ex: "Bitcoin vs. USD" pode ser crypto e finance ao mesmo tempo). O scanner conta cada mercado uma única vez.

### O resultado

Após um scan, o `market_cache` fica atualizado com centenas (às vezes mais de mil) de mercados. Esses são os candidatos que os filtros vão examinar.

### Inspecionando o resultado

```bash
python scanner.py  # roda o scanner e mostra os primeiros 3 mercados
```

---

## `filters.py` — As 8 Regras de Entrada

**Decide quais mercados são elegíveis para uma estratégia.**

É aqui que o bot diz "não" para a maioria dos mercados. Cada filtro é uma regra simples. Dado o mesmo mercado e estratégia, a resposta é sempre a mesma — sem aleatoriedade, sem IA.

### Os 8 filtros (na ordem em que rodam)

1. **`filter_active`** — O mercado está ativo? Não está fechado? *Rápido, O(1)*

2. **`filter_category`** — A categoria do mercado está na lista permitida? *Rápido, O(1)*

3. **`filter_price`** — O preço está dentro do limite?
   - Penny: YES ≤ 4¢
   - NO Sist.: NO ≤ 50¢
   - Rejeita se preço = 0 (sem livro de ordens)

4. **`filter_liquidity`** — Tem pelo menos $1.000 em liquidez? *Sem liquidez = difícil comprar/vender*

5. **`filter_expiry`** — Vence entre 14 e 200 dias? *Muito perto = sem tempo. Muito longe = capital preso.*

6. **`filter_no_duplicate`** — Já tem posição aberta neste mercado? *Evita entrar duas vezes no mesmo lugar*

7. **`filter_max_positions`** — Atingiu o limite de posições abertas da estratégia? *Controle de exposição total*

8. **`filter_max_per_event`** — Já tem 3 posições no mesmo evento? *Evita concentrar tudo num único resultado*

### Por que a ordem importa

Os filtros mais rápidos ficam primeiro. Se um mercado falha na categoria (O(1)), o bot nem chega a consultar o banco de dados para checar duplicatas (O(n)). Isso mantém o pipeline eficiente mesmo com mil mercados para avaliar.

### O que você vê quando um mercado é rejeitado

```
mkt_xyz: filter_price → preço YES=0.0500 > max=0.0400
mkt_abc: filter_category → categoria 'politics' não permitida
mkt_def: filter_max_per_event → 3 posições no evento 'evt_001' >= max=3
```

---

## `strategy.py` — O Motor de Decisão

**Calcula o valor esperado e o tamanho de cada posição.**

Recebe os mercados que passaram nos filtros e decide: vale entrar? E se sim, com quanto?

Não há IA aqui. São fórmulas matemáticas clássicas, verificáveis na mão.

### Valor Esperado (EV)

A fórmula: `EV = (chance de ganhar × ganho) − (chance de perder × custo)`

Exemplo penny (YES a $0.03, win rate 5%):
```
EV = 0.05 × $0.97  −  0.95 × $0.03
   = $0.0485        −  $0.0285
   = $0.02 por share
```

Se o EV for negativo, o bot não entra. Simples assim.

### Sizing com Quarter-Kelly

Kelly completo diz quanto do bankroll colocar em cada aposta para maximizar crescimento de longo prazo. O problema é que Kelly completo é arriscado — presume que suas estimativas de win rate são perfeitas.

**Quarter-Kelly** usa apenas 25% do que Kelly recomendaria. Mais conservador, resiste melhor a erros nas estimativas.

Exemplo: com bankroll de $1.000 e penny a $0.03, o bot aloca ~$5 por posição (~171 shares).

### Take Profit e Stop Loss

Calculados na hora da entrada e salvos na posição:

- **Penny:** TP a 3x (entra a $0.03, sai a $0.09) | SL a -50% (sai a $0.015)
- **NO Sist.:** TP a 1.5x (entra a $0.30, sai a $0.45) | SL a -50% (sai a $0.15)

### Ranking por EV%

Se há 50 oportunidades mas só 20 slots disponíveis, o bot prioriza as com maior EV percentual (EV dividido pelo custo). A menor aposta em termos absolutos pode ser a melhor em termos de retorno sobre capital.

---

## `paper_engine.py` — O Executor de Ordens

**Transforma sinais em posições — seja no simulado, seja no real.**

É o módulo que realmente "compra" e "vende". Em modo paper, salva tudo no SQLite como se a ordem tivesse sido executada ao preço atual. Em modo live (quando implementado), chamaria a CLOB API da Polymarket.

### A troca paper → live é uma linha

```python
# No config.py:
MODE = "live"  # antes era "paper"
```

A lógica de decisão não muda — só o executor muda.

### O que fica registrado em cada entrada

- Preço de entrada, quantidade de shares, custo total
- Token ID (necessário para consultar preços depois)
- Take profit e stop loss calculados
- `bounce_exit_pct` — a regra de saída em bounce da estratégia

### Verificação de condições de saída

O `check_exit_conditions()` é chamado pelo monitor em cada ciclo:

```
Preço atual vs. target_exit → take_profit
Preço atual vs. stop_price → stop_loss
Mercado resolvido? → resolved_win ou resolved_loss
```

A resolução tem prioridade — se um mercado resolve YES enquanto o preço está abaixo do stop, o bot fecha como win (pagamento de $1.00), não como stop loss.

---

## `monitor.py` — O Vigia de Posições

**Verifica as posições abertas a cada 5 minutos e age quando necessário.**

É o módulo que roda em loop durante toda a operação do bot. Cada ciclo demora menos de um segundo na prática.

### O que acontece em cada ciclo

1. **Carrega** todas as posições abertas do banco
2. **Busca preços** em batch (uma única request para todos os tokens)
3. **Para cada posição**:
   - Atualiza o `current_price` no banco
   - Verifica se houve bounce (variação > 10%)
   - Verifica se o mercado resolveu
   - Verifica TP e SL
   - Executa a saída se necessário

### Bounce: diferente por estratégia

**Penny:** se o preço disparar 50%, 100%, 200% — apenas alerta no Telegram. Não fecha. Motivo: o payoff de $1.00 na resolução é o que faz a estratégia funcionar. Fechar um winner cedo destrói o EV.

**NO Sistemático:** se o preço subir o suficiente para capturar ≥ 50% do caminho até o take profit — fecha automaticamente. O perfil de retorno mais simétrico justifica realizar o lucro parcial.

### Resolução de mercados

Quando a Polymarket encerra um mercado, o bot detecta isso no `market_cache` e fecha a posição com o preço correto:

- Ganhou → preço de saída = $1.00 → PnL máximo
- Perdeu → preço de saída = $0.00 → perde o custo total

### Falha na API

Se a CLOB API falhar durante o ciclo, o bot registra o erro, notifica o Telegram e continua — sem fechar nada. Melhor não agir do que agir com dados errados.

---

## `analytics.py` — O Analista

**Calcula métricas, gera relatórios e exporta logs completos.**

Responde às perguntas: estou ganhando? Qual estratégia performa melhor? Meu EV real está perto do teórico?

### Métricas calculadas

Para cada corte (geral, por estratégia, por categoria):

- **Win rate** — percentual de apostas vencedoras
- **PnL total** — ganho ou perda acumulada em dólares
- **ROI** — retorno sobre capital investido
- **Profit factor** — quanto ganhou por cada dólar perdido (> 1 = lucrativo)
- **Avg win / avg loss** — ticket médio de vitória e derrota
- **Melhor e pior trade** — os extremos do histórico
- **Tempo médio de hold** — quanto tempo as posições ficam abertas em média
- **PnL não realizado** — quanto as posições abertas valem agora vs. o que pagou
- **EV teórico vs. EV real** — o que o modelo previa vs. o que aconteceu

### EV teórico vs. real

Esta é a métrica mais importante para calibrar as estratégias. Se o EV real estiver consistentemente abaixo do teórico, as premissas (como win rate estimada) precisam ser revisadas.

### Log completo de transações

O `get_full_trade_log()` retorna todas as posições (abertas e fechadas) com 21 campos por linha, em ordem cronológica. Inclui:

- `hold_hours` — quantas horas a posição ficou aberta
- `pnl_pct` — PnL como percentual do custo
- Todos os dados de entrada e saída

### Exports

```python
analytics.export_trade_log_csv()   # → CSV para Excel/Sheets
analytics.export_trade_log_json()  # → JSON para scripts externos
analytics.export_report_json()     # → Relatório completo estruturado
```

### Daily Digest

O `format_daily_digest()` gera a mensagem que vai para o Telegram uma vez por dia — um resumo completo do estado do portfolio em formato legível.

---

## `telegram_bot.py` — O Mensageiro

**Envia alertas formatados para o Telegram.**

Unidirecional — o bot só envia mensagens, não recebe comandos. Se quiser interagir, use a linha de comando.

### Tipos de alerta

| Mensagem | Quando aparece |
|---|---|
| 🟢 **Nova Entrada** (YES) | Posição penny aberta |
| 🔵 **Nova Entrada** (NO) | Posição NO sistemático aberta |
| 🎯 **Exit: Take Profit** | Preço atingiu o alvo |
| 🛑 **Exit: Stop Loss** | Preço caiu abaixo do stop |
| 📈 **Exit: Bounce Exit** | NO sist. fechou em bounce lucrativo |
| 🏆 **Mercado Resolvido** (win) | Acertou a resolução |
| 💀 **Mercado Resolvido** (loss) | Errou a resolução |
| 🔺 **Bounce UP** | Variação positiva > 10% (penny: só alerta) |
| 🔻 **Bounce DOWN** | Variação negativa > 10% |
| 📊 **Daily Digest** | Resumo diário do portfolio |
| 🔍 **Scan Completo** | Resumo do scan com novas entradas |
| ⚠️ **Erro do Sistema** | Falha de API ou erro inesperado |
| 🚨 **Drawdown Alert** | Drawdown ultrapassou 20% |

### Se o Telegram não estiver configurado

O bot funciona normalmente — só não envia mensagens. Todas as informações também aparecem nos logs do terminal.

### Implementação simples

Usa apenas `requests.post` direto na API do Telegram. Sem SDK — a API do Telegram para envio é simples o suficiente para não precisar de dependência extra.

---

## `run.py` — A Interface de Linha de Comando

**O ponto de entrada. O que você roda no terminal.**

Todos os módulos podem ser usados isoladamente em código, mas o `run.py` organiza tudo em comandos prontos para uso.

### Comandos disponíveis

**`scan`** — Executa o pipeline completo de descoberta e entrada:
1. Busca mercados na Gamma API (por categoria)
2. Filtra elegíveis para cada estratégia
3. Gera sinais ordenados por EV%
4. Executa as entradas
5. Notifica o Telegram

**`monitor`** — Um ciclo de monitoramento:
1. Busca preços das posições abertas
2. Detecta bounces, resoluções, TP e SL
3. Executa saídas necessárias
4. Notifica o Telegram

**`report`** — Relatório detalhado no terminal (sem Telegram):
- Métricas gerais do portfolio
- Desempenho por estratégia
- Desempenho por categoria
- Contagem de exits por motivo

**`status`** — Resumo em 6 linhas: posições abertas, win rate, PnL, ROI.

**`digest`** — Gera o daily digest e envia ao Telegram (também imprime no terminal).

**`export`** — Exporta os dados para análise:
- `trade_log_YYYYMMDD.csv` — log completo em CSV
- `trade_log_YYYYMMDD.json` — mesmo em JSON
- `report_YYYYMMDD.json` — métricas estruturadas

**`loop`** — Ciclo contínuo que roda tudo automaticamente:
- Scan a cada 1 hora (scan imediato no startup)
- Monitor a cada 5 minutos
- Daily digest ao virar o dia UTC
- Snapshot automático diário
- Graceful shutdown com Ctrl+C

### Opções globais

```bash
--verbose / -v    Ativa logs DEBUG (muitos detalhes)
--bankroll / -b   Define o bankroll para sizing (padrão: $1.000)
```

### Variáveis de ambiente úteis

```bash
BOT_MODE=live            # Ativa modo live (requer config extra)
BOT_DB_PATH=meu.db       # Caminho customizado para o banco
BOT_SNAPSHOTS_DIR=bkp/   # Diretório de snapshots
TELEGRAM_TOKEN=xxx        # Token do bot do Telegram
TELEGRAM_CHAT_ID=yyy      # Seu chat ID no Telegram
```

---

## Fluxo de dados completo

Para entender como tudo se conecta, aqui está o caminho de um mercado desde o scan até o relatório:

```
Gamma API
    ↓
gamma_client.fetch_events_by_tag()
    ↓ eventos com mercados embutidos
gamma_client.normalize_market()
    ↓ dict padronizado com preços, tokens, etc.
scanner._process_event()
    ↓ classifica categoria, faz upsert
state.upsert_market()  →  market_cache (SQLite)
    ↓
state.get_active_markets()
    ↓ centenas de mercados do cache
filters.filter_markets()
    ↓ apenas os elegíveis
strategy.generate_signals()
    ↓ TradeSignal com EV, shares, TP, SL
paper_engine.execute_entry()
    ↓
state.open_position()  →  positions (SQLite)
    ↓
[5 minutos depois]
    ↓
clob_client.get_midpoints()
    ↓ preços atuais
monitor.run_cycle()
    ↓ detecta TP/SL/bounce/resolução
paper_engine.execute_exit()
    ↓
state.close_position()  →  positions (SQLite, status=closed)
    ↓
analytics.compute_metrics()
    ↓ win rate, PnL, EV real vs teórico
analytics.format_daily_digest()
    ↓
telegram_bot.notify_daily_digest()  →  sua mensagem no Telegram
```

---

## Dicas de calibração

Após rodar por algumas semanas em paper trading, compare:

1. **EV teórico vs. EV real** — se o real estiver muito abaixo, revise `base_win_rate`
2. **Win rate real** — se diferente do `base_win_rate` configurado, ajuste
3. **Profit factor** — abaixo de 1.0 significa que está perdendo mais do que ganhando
4. **Hold time médio** — se as posições ficam muito pouco tempo abertas, o SL pode estar muito apertado
5. **Exit reasons** — muitos `stop_loss`? Considere aumentar o `stop_loss` fraction ou revisar os filtros de liquidez
6. **Drawdown alerts** — se aparecem frequentemente, considere reduzir `kelly_fraction`

O comando `python run.py export` gera o CSV para você explorar no Excel ou Google Sheets com seus próprios filtros e gráficos.
