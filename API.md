# API da Polymarket — Referência para o Bot

> Fonte: <https://docs.polymarket.com/api-reference/introduction>
> Última revisão: 2026-04-16

Este documento é a fonte de verdade do projeto sobre as APIs da Polymarket.
Consulte antes de adicionar qualquer chamada nova. Se a doc oficial mudar,
atualize aqui também — o código foi escrito contra o que está escrito abaixo.

---

## 1. As três APIs (+ uma)

A Polymarket divide o serviço em **três APIs separadas**, cada uma com seu
próprio domínio e responsabilidade. Existe ainda uma quarta (Bridge) que o
bot não usa.

| API | Base URL | Auth | Uso no bot |
|---|---|---|---|
| **Gamma** | `https://gamma-api.polymarket.com` | ❌ Pública | Descoberta de mercados, eventos, tags |
| **Data** | `https://data-api.polymarket.com` | ❌ Pública | Posições do usuário (modo live), leaderboards |
| **CLOB** | `https://clob.polymarket.com` | Mista (ver abaixo) | Preços (público), ordens (autenticado) |
| Bridge | `https://bridge.polymarket.com` | — | **Não usado** (depósitos/saques via fun.xyz) |

**Regra fundamental**: Gamma e Data são 100% públicas. CLOB tem endpoints
públicos (orderbook, preços) e autenticados (ordens).

---

## 2. Autenticação

### 2.1 O que é público
Qualquer coisa em Gamma, Data e os endpoints de leitura do CLOB
(`/midpoint`, `/price`, `/book`, `/prices-history`, etc.). O bot em modo
**paper trading não precisa de nenhuma credencial**.

### 2.2 O que é autenticado (modo live apenas)
Tudo que cria ou cancela ordem precisa dos 5 headers L2:

| Header | Descrição |
|---|---|
| `POLY_ADDRESS` | Endereço Polygon do signer |
| `POLY_SIGNATURE` | HMAC-SHA256 do request |
| `POLY_TIMESTAMP` | UNIX timestamp atual |
| `POLY_API_KEY` | API key (UUID) |
| `POLY_PASSPHRASE` | Passphrase gerada com a API key |

### 2.3 Modelo de dois níveis
- **L1** — assina com a chave privada da wallet (EIP-712). Usado **apenas**
  para obter ou derivar as credenciais L2. Tudo mais usa L2.
- **L2** — HMAC-SHA256 com a API secret. Usado em todo trading.

**Não implementar isso na mão.** Quando for para live, usar o SDK oficial
`py-clob-client` (ver §5). Implementar EIP-712 corretamente é delicado e
é o tipo de coisa que não vale o risco.

### 2.4 Signature types (para o modo live)

| Tipo | Valor | Quando usar |
|---|---|---|
| EOA | `0` | Wallet padrão (MetaMask). Funder = EOA. Precisa POL para gás. |
| POLY_PROXY | `1` | Email/Google login via Magic Link (raro para bot) |
| GNOSIS_SAFE | `2` | **Padrão para quem fez login na polymarket.com** |

**A wallet que aparece no perfil do usuário em polymarket.com é a proxy
wallet** (geralmente Gnosis Safe). Essa é a `funder`, não o EOA.

---

## 3. Rate Limits (oficiais, Cloudflare)

Importante: quando o limite é atingido, **requests são throttled (enfileirados)**,
não rejeitados com 429 imediatamente. Isso muda a estratégia de retry.

### 3.1 Gamma API (onde o scanner vive)

| Endpoint | Limite |
|---|---|
| Geral | 4.000 req / 10s |
| `/events` | **500 req / 10s** ← o scanner usa este |
| `/markets` | 300 req / 10s |
| `/markets` + `/events` combinados | 900 req / 10s |
| `/tags` | 200 req / 10s |
| `/public-search` | 350 req / 10s |
| `/comments` | 200 req / 10s |

### 3.2 Data API (usado em modo live para verificar posições)

| Endpoint | Limite |
|---|---|
| Geral | 1.000 req / 10s |
| `/trades` | 200 req / 10s |
| `/positions` | 150 req / 10s |
| `/closed-positions` | 150 req / 10s |

### 3.3 CLOB API (monitor de preços + trading)

| Endpoint | Limite |
|---|---|
| Geral | 9.000 req / 10s |
| `/midpoint` | **1.500 req / 10s** ← o monitor usa este |
| `/midpoints` (batch) | 500 req / 10s |
| `/book` | 1.500 req / 10s |
| `/price` | 1.500 req / 10s |
| `/prices-history` | 1.000 req / 10s |

Trading (requer auth):

| Endpoint | Burst | Sustained |
|---|---|---|
| `POST /order` | 3.500 / 10s | 36.000 / 10min |
| `DELETE /order` | 3.000 / 10s | 30.000 / 10min |
| `DELETE /cancel-all` | 250 / 10s | 6.000 / 10min |

### 3.4 Cálculo para o nosso uso

**Scanner** (1x/hora, 4 categorias × paginação):
- Pior caso: 20 páginas × 4 categorias = 80 req em ~10s → **~1,6% do limite**.

**Monitor** (5 min, até 200 posições):
- 200 req a `/midpoint` a cada 5 min = 0,67 req/s média. **Muito abaixo de 1500/10s**.
- Se quiser consolidar, `/midpoints` POST com body aceita múltiplos tokens.

Conclusão: nosso uso é trivialmente seguro. A única preocupação real é evitar
*spam accidental* em caso de bug (loop infinito). O throttle interno de 100ms
que já coloquei no `gamma_client` é suficiente.

---

## 4. Endpoints usados pelo bot

Lista enxuta. A documentação oficial lista ~80 endpoints — apenas uma fração
importa para este projeto.

### 4.1 Descoberta de mercados (Scanner) — Gamma API

#### `GET /events` — Listar eventos com filtros
**Uso:** descoberta principal. Retorna eventos (com seus mercados embutidos)
filtrando por tag, status, liquidez, etc.

**Query params importantes:**
- `tag_id=21` — filtra por categoria (ver §6 para IDs)
- `related_tags=true` — inclui sub-tags da categoria (ex: BTC dentro de Crypto)
- `closed=false` — só mercados não resolvidos
- `active=true` — só ativos
- `limit=100` / `offset=N` — paginação (limite aceito: 1–500; usamos 100)
- `order=volume24hr` + `ascending=false` — ordenar por volume decrescente
- `liquidity_min=1000` — filtro server-side (pode evitar request desnecessário)

**Nota:** a doc também mostra `list-events-keyset-pagination` (paginação por
cursor), mais eficiente para datasets grandes. Para nosso uso (poucas
centenas de mercados por categoria) o offset é suficiente.

#### `GET /tags` — Listar todas as tags
Útil para descobrir IDs novos quando a Polymarket criar categorias. Ver §6.

### 4.2 Preços em tempo real (Monitor) — CLOB API

#### `GET /midpoint?token_id=X` — Midpoint de 1 token

```bash
curl https://clob.polymarket.com/midpoint?token_id=TOKEN_ID
# → { "mid_price": "0.45" }
```

**Observações:**
- Retorna o preço médio entre best bid e best ask
- `mid_price` vem como **string** (não float) — precisa converter
- O `token_id` é o valor em `clobTokenIds[0]` (YES) ou `clobTokenIds[1]` (NO)
  que vem do Gamma

#### `POST /midpoints` — Batch de até N tokens
Evita N requests para monitorar muitas posições. **Preferir em produção**.

#### `GET /prices-history?market=TOKEN_ID&interval=1h` — Histórico
Usado para analytics (calcular performance teórica). Não usado no hot path.

### 4.3 Modo live apenas (não implementado ainda)

#### `POST /order` — Nova ordem (precisa L2 auth)
#### `DELETE /order/{id}` — Cancelar ordem
#### `GET /orders` — Ordens abertas do usuário

Ver §5 — usar `py-clob-client` ao invés de HTTP direto.

### 4.4 NÃO usados (mas bom saber que existem)

- `/trades` — histórico de trades do mercado (útil se quiser calibrar EV)
- `/spreads` — spreads atuais (útil para estratégias de market making)
- `/leaderboards` — rankings de traders
- `/comments` — comentários sociais
- `/open-interest` — open interest por evento
- WebSocket Market Channel — push de preços (alternativa ao polling)

---

## 5. Clientes oficiais

A Polymarket mantém SDKs oficiais em TypeScript, Python e Rust. O bot usa
HTTP direto para a Gamma API (simples, sem auth) mas **deve usar o SDK para
CLOB autenticado**.

| Linguagem | Pacote | Repo |
|---|---|---|
| TypeScript | `@polymarket/clob-client` | github.com/Polymarket/clob-client |
| **Python** | **`py-clob-client`** | github.com/Polymarket/py-clob-client |
| Rust | `polymarket-client-sdk` | github.com/Polymarket/rs-clob-client |

**Quando usarmos modo live**, adicionar `py-clob-client` ao `requirements.txt`
e o `paper_engine.py` terá um backend "live" que instancia `ClobClient` e
chama `create_and_post_order()`.

### Setup exemplo (para referência futura)

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,  # Polygon mainnet
    key=os.getenv("PRIVATE_KEY"),      # EOA private key
    creds=api_creds,                    # Gerado com create_or_derive_api_creds()
    signature_type=2,                   # GNOSIS_SAFE (padrão)
    funder=os.getenv("FUNDER_ADDRESS"), # Proxy wallet da polymarket.com
)

# Obter credenciais na primeira vez:
# creds = client.create_or_derive_api_creds()
# Salvar creds com cuidado — perdeu o nonce, perdeu acesso.
```

---

## 6. Sistema de tags (categorias)

A Polymarket organiza mercados por **tags com IDs numéricos oficiais**. Os IDs
abaixo vieram do repositório oficial `safe-wallet-integration` da Polymarket.
Confirme com `GET /tags` antes de confiar em IDs novos.

| Categoria | tag_id | Notas |
|---|---|---|
| Politics | 2 | Mercados políticos |
| Finance | 120 | Fed, taxa de juros, economia |
| Crypto | 21 | BTC, ETH, + sub-tags |
| Sports | 100639 | Todos os esportes |
| Tech | 1401 | Tecnologia |
| Entertainment / Culture | 596 | Oscar, Grammy, etc. |
| Geopolitics | 100265 | Conflitos, tratados |

**Weather não existe como tag oficial top-level.** Mercados de clima caem em
"Current Events" ou outra tag genérica. Se precisar filtrar clima, vai ter que
fazer por keyword no `question` ou inspecionar `/tags` para achar uma tag
mais específica.

### Como uma tag aparece na resposta

No evento retornado por `/events`:

```json
{
  "id": "16085",
  "title": "How many Fed rate cuts in 2025?",
  "tags": [
    { "id": 120, "label": "Finance", "slug": "finance" },
    { "id": 99999, "label": "Fed", "slug": "fed" }
  ],
  "markets": [ ... ]
}
```

Um mercado pode ter múltiplas tags. O bot classifica pela primeira tag
conhecida que aparecer (função `classify_market_by_tags` em `config.py`).

---

## 7. Estrutura hierárquica: Event → Market → Outcome

Entender isto evita 90% dos bugs de integração:

```
Event                      ← pergunta de alto nível
 ├── slug                    "how-many-fed-rate-cuts-in-2025"
 ├── title                   "How many Fed rate cuts in 2025?"
 ├── id                      "16085"
 ├── tags[]                  [Finance, Fed]
 └── markets[]               ← N opções tradáveis
      ├── Market A           ← uma opção específica
      │    ├── id              "521234"
      │    ├── conditionId     "0xabc..."
      │    ├── question        "Will there be 3 rate cuts?"
      │    ├── outcomes        '["Yes","No"]'        ← STRING-JSON
      │    ├── outcomePrices   '["0.04","0.96"]'     ← STRING-JSON
      │    ├── clobTokenIds    '["0xyes","0xno"]'    ← STRING-JSON
      │    └── outcomes (tradables)
      │         ├── Yes token → ID em clobTokenIds[0]
      │         └── No token  → ID em clobTokenIds[1]
      └── Market B (outra opção do mesmo evento)
```

**Invariante matemática**: `1 share YES + 1 share NO = $1.00 garantido`.
Isso é explorado pela estratégia "NO sistemático" — se NO está a $0.30,
implicitamente YES está a $0.70. Se NO resolver como `true`, ganha $0.70 por
share investido.

### Gotcha crítico: campos string-JSON

**Três campos retornam como string** contendo JSON array:
- `outcomes` → `'["Yes","No"]'`
- `outcomePrices` → `'["0.04","0.96"]'`
- `clobTokenIds` → `'["0x...","0x..."]'`

Precisa `json.loads()` para virar lista. Nosso `gamma_client._parse_json_string_list`
trata isso de forma defensiva.

---

## 8. Geoblock (regulatório)

Polymarket bloqueia ordens de certos países. Endpoint para verificar:

```bash
GET https://polymarket.com/api/geoblock
# → { "blocked": true, "ip": "...", "country": "US", "region": "NY" }
```

### 8.1 Brasil 🇧🇷

**BR não está na lista de bloqueados.** Países totalmente bloqueados incluem
US, DE, FR, GB, IT, NL, RU, BE, entre outros. Close-only (só fechar posições
existentes): PL, SG, TH, TW.

**Para este bot, operando do Brasil, não há restrição formal de acesso à API**.
Mas:
- **Modo paper**: funciona 100% sem qualquer preocupação
- **Modo live**: mesmo sem bloqueio formal, recomenda-se chamar
  `/api/geoblock` antes de cada sessão de trading. Se a Polymarket adicionar
  BR no futuro, o bot detecta e para de tentar ordens

### 8.2 Infraestrutura
Os servidores primários ficam em `eu-west-2`. Latência do Brasil para Europa
é ~180-250ms — aceitável para o polling de 5min do monitor.

---

## 9. Revisão do código atual contra a documentação

Revisei `gamma_client.py` e `scanner.py` contra tudo acima. Resultado:

### ✅ Corretos
- Base URL da Gamma (`https://gamma-api.polymarket.com`)
- Uso de `/events` com `tag_id`, `closed`, `active`, `limit`, `offset`
- Parsing defensivo de `outcomePrices`, `clobTokenIds`, `outcomes` como string-JSON
- Retry em 429/5xx (embora a doc diga que Cloudflare dá throttle, não 429)
- Sem auth no scanner (correto — Gamma é pública)
- Throttle interno de 100ms é conservador mas OK

### ⚠️ Melhorias possíveis (não críticas)
1. **Poderia aceitar `liquidity_min` direto no scan** — A API aceita esse
   filtro server-side em `/events`. Hoje buscamos tudo e filtramos no
   `filters.py`. Não é bug, só ineficiência — transfere dados desnecessários.
2. **Ordenação** — podemos passar `order=volume24hr&ascending=false` para
   pegar primeiro os mercados com mais volume.
3. **Keyset pagination** — para datasets grandes, a doc recomenda
   `list-events-keyset-pagination` sobre offset. Mudaria a assinatura do
   iterator, mas é mais robusto. Adiar.

### ❌ Errados / faltando
1. **`page_size=100` sem validar limite máximo da API** — doc diz que o
   limit aceito vai até certo valor. 100 está dentro do seguro, mas vale
   documentar a escolha.
2. **Falta consultar `POLYMARKET_TAGS` dinamicamente** — IDs são hardcoded.
   Se a Polymarket mudar um ID (improvável, mas), o bot quebra silenciosamente.
   Idealmente, no startup, chamar `GET /tags` e validar. Adiar — não é crítico.
3. **Monitor ainda não existe** — mas quando existir, vai usar CLOB
   `/midpoint?token_id=X`, não a Gamma.

### 🔒 Para modo live (futuro)
- Adicionar `py-clob-client>=0.17.0` ao `requirements.txt`
- `paper_engine.py` precisa de branch "live" que usa `ClobClient.create_and_post_order`
- Chamar `GET /api/geoblock` no startup do modo live
- Armazenar credenciais CLOB de forma segura (fora do repo, env vars)
- Obter `funder` address do perfil polymarket.com do usuário

---

## 10. Referências

- **Introdução:** <https://docs.polymarket.com/api-reference/introduction>
- **Auth:** <https://docs.polymarket.com/api-reference/authentication>
- **Rate limits:** <https://docs.polymarket.com/api-reference/rate-limits>
- **SDKs:** <https://docs.polymarket.com/api-reference/clients-sdks>
- **Geoblock:** <https://docs.polymarket.com/api-reference/geoblock>
- **List events:** <https://docs.polymarket.com/api-reference/events/list-events>
- **Midpoint:** <https://docs.polymarket.com/api-reference/data/get-midpoint-price>
- **SDK Python:** <https://github.com/Polymarket/py-clob-client>
- **WebSocket:** <https://docs.polymarket.com/api-reference/wss/market>

---

_Se você encontrar divergência entre este documento e a doc oficial, a oficial
ganha — atualize este arquivo._
