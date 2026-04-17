# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A **paper/live trading bot for Polymarket** (prediction markets). Monitors binary markets and applies two strategies:
- **penny**: buys YES shares at ≤ $0.04 (high-risk, low-cost, high-upside bets)
- **no_systematic**: buys NO shares at ≤ $0.50 (exploits YES-biased mispricing)

Currently in paper trading mode. Live trading (real orders via CLOB API) is not yet implemented.

## Running tests

Tests are plain Python scripts — no test runner, just execute directly:

```bash
python test_foundation.py    # config + state
python test_categories.py    # category whitelist + per-strategy override
python test_tags.py          # tag_id classification
python test_scanner.py       # market discovery (mocked)
python test_api_clients.py   # CLOB + geoblock (mocked)
python test_filters.py       # 8 filters + full pipeline

python test_live_api.py      # hits real Polymarket APIs — requires internet
python test_filters_live.py  # filtros rodando sobre mercados reais (sem mock, sem auth)
```

All test files must be run from the project root directory.

On Windows, run with `python -X utf8 <file>` to avoid cp1252 encoding errors with `✓` characters.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BOT_MODE` | `paper` | `"paper"` or `"live"` |
| `TELEGRAM_TOKEN` | `YOUR_TOKEN_HERE` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | `YOUR_CHAT_ID_HERE` | Telegram chat ID |
| `BOT_DB_PATH` | `data/positions.db` | SQLite database path |
| `BOT_SNAPSHOTS_DIR` | `data/snapshots` | JSON snapshot directory |

## Architecture

### Data flow

```
Gamma API → gamma_client → normalize_market() → filters.apply_all() → state.open_position()
                                                                             ↓
CLOB API → clob_client → get_midpoints() → monitor → state.close_position() (TP/SL)
```

### Module responsibilities

- **`config.py`** — Single source of truth for all constants. `StrategyParams` is a frozen dataclass; all thresholds live here. Add a new strategy by instantiating `StrategyParams` and adding it to `STRATEGIES`. Contains `POLYMARKET_TAGS` (dict `{category: tag_id}`), `ALLOWED_CATEGORIES` (global whitelist, excludes politics), and `resolve_allowed_categories(strategy)` (returns per-strategy override or global whitelist).

- **`gamma_client.py`** — Fetches and normalizes markets from Gamma API. Key function: `fetch_events_by_tag()` (paginating iterator) + `normalize_market()` (flattens raw API dict into the canonical market dict used everywhere else). Has 100ms throttle between requests.

- **`clob_client.py`** — Gets live prices from CLOB API. `get_midpoints()` (batch POST) is preferred over `get_midpoint()` (single GET) when monitoring many positions.

- **`filters.py`** — Pure filter functions. Each filter is `(market_dict, StrategyParams, **kwargs) → FilterResult(passed, reason)` — rejections always carry an explicit reason. `ALL_FILTERS` defines execution order (cheap/no-I/O first, SQLite queries last): `filter_active` → `filter_category` → `filter_price` → `filter_liquidity` → `filter_expiry` → `filter_no_duplicate` → `filter_max_positions` → `filter_max_per_event`. Two public entry points: `apply_all()` returns a `FilterSummary` with full diagnostics (use for Telegram alerts); `filter_markets()` returns `(eligible_list, all_summaries)` (use in the scan → filter → strategy pipeline). `filter_category` delegates to `resolve_allowed_categories()` in `config.py`, which merges the global whitelist with any per-strategy `allowed_categories` override.

- **`state.py`** — SQLite persistence (WAL mode). Three tables: `positions`, `trades_history`, `market_cache`. `StateManager` is the only class that touches the DB. Every `open_position()` call also writes to `trades_history`.

- **`geoblock.py`** — Call `check_geoblock()` before any live trading session. Brasil (BR) is not blocked. The offline lists (`FULLY_BLOCKED_COUNTRIES`, `CLOSE_ONLY_COUNTRIES`) are snapshots — always prefer the live endpoint.

- **`API.md`** — Internal reference doc for all Polymarket API details. Consult before adding any new API call. Update it when the official docs change.

## Critical API gotchas

1. **Three Gamma API fields come as string-encoded JSON**, not arrays — always use `_parse_json_string_list()` or `json.loads()`:
   - `outcomePrices` → `'["0.04","0.96"]'`
   - `clobTokenIds` → `'["0xyes","0xno"]'`
   - `outcomes` → `'["Yes","No"]'`

2. **CLOB `/midpoint` returns `"mid"`, not `"mid_price"`** despite the official docs. The client handles both (`data.get("mid") or data.get("mid_price")`).

3. **Tags belong to events, not markets.** The Gamma API embeds markets inside events; `normalize_market()` takes the parent event as a second argument to extract tags and `event_id`.

4. **YES index is always 0, NO index is always 1** in `outcomePrices` and `clobTokenIds` for binary markets.

## Adding a new strategy

1. Define a new `StrategyParams` instance in `config.py`
2. Add it to the `STRATEGIES` dict
3. If the strategy needs a category whitelist different from the global default, use the `allowed_categories` field on `StrategyParams`

## Live filter test results (2026-04-16)

`test_filters_live.py` roda o pipeline completo sobre mercados reais. Parâmetros da rodada:

| Parâmetro | Valor |
|---|---|
| Categorias | crypto, politics, finance, sports, tech, entertainment, geopolitics |
| `liquidity_min` (server-side) | $1.000 |
| `max_pages` por categoria | 5 (500 mercados) |
| Total coletado | **24.444 mercados** |

### PENNY_STRATEGY (YES ≤ $0.04)

**1.202 elegíveis (4,9%)**

| Filtro | Rejeitados | % do total |
|---|---|---|
| `filter_price` | 9.476 | 38,8% |
| `filter_active` | 8.071 | 33,0% |
| `filter_category` | 3.043 | 12,4% |
| `filter_expiry` | 2.071 | 8,5% |
| `filter_liquidity` | 581 | 2,4% |

Amostra de elegíveis:
```
[crypto] YES=0.021  liq=$  168,356  Will Bitcoin dip to $55,000 in April?
[crypto] YES=0.010  liq=$  254,628  Will Bitcoin dip to $50,000 in April?
[crypto] YES=0.003  liq=$1,111,893  Will Bitcoin reach $150,000 in April?
```

### NO_SYSTEMATIC_STRATEGY (NO ≤ $0.50)

**288 elegíveis (1,2%)**

| Filtro | Rejeitados | % do total |
|---|---|---|
| `filter_price` | 10.174 | 41,6% |
| `filter_active` | 8.071 | 33,0% |
| `filter_category` | 3.043 | 12,4% |
| `filter_expiry` | 1.723 | 7,0% |
| `filter_liquidity` | 1.145 | 4,7% |

Amostra de elegíveis:
```
[crypto]  NO=0.375  liq=$37,010  MegaETH market cap (FDV) >$800M one day after launch?
[crypto]  NO=0.375  liq=$13,271  Will Solana reach $90 in April?
[finance] NO=0.061  liq=$150,797 Will Kevin Warsh be confirmed as Fed Chair?
[finance] NO=0.095  liq=$135,740 Will there be no change in Fed interest rates after June 2026?
```

### Sanidade dos dados normalizados

- Soma YES + NO sempre em [0.95, 1.05] — zero anomalias de preço
- 439 mercados sem `end_date` — corretamente rejeitados por `filter_expiry`
- 39 mercados sem `yes_token_id` — mercados não-binary ou incompletos

### Interpretação dos resultados

`filter_price` é o maior gargalo (39–42%): o universo de mercados Polymarket é dominado por contratos com probabilidade alta (YES > 4¢). `filter_active` elimina um terço — grande estoque de mercados já encerrados permanece indexado na API. `filter_category` retira 12,4% porque politics está fora da whitelist das duas estratégias. `filter_liquidity` pesa mais para NO sistemático (4,7% vs 2,4%) — mercados com YES muito alto (NO barato) tendem a ter liquidez menor.

## Future live mode

When implementing live trading:
- Add `py-clob-client` to `requirements.txt`
- Call `check_geoblock()` at startup
- Use `ClobClient` from `py-clob-client` for orders — do not implement EIP-712 signing manually
- Store CLOB credentials (API key, passphrase, private key) in env vars only, never in code
- The wallet shown on polymarket.com is the **proxy/funder address** (Gnosis Safe), not the EOA
