"""
test_live_api.py — Testes de acesso REAL às APIs da Polymarket.

Roda chamadas reais (sem mock) para verificar:
  1. Geoblock — BR está liberado?
  2. Gamma API — /events responde? Estrutura dos campos confere?
  3. CLOB API — /midpoint retorna preço? /midpoints batch funciona?

Não persiste nada, não abre posição, não precisa de auth.
"""

import json
import sys
import time

sys.path.insert(0, ".")
import requests

# ─── Cores no terminal ────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}[OK]{RESET} {msg}")
def fail(msg): print(f"  {RED}[FAIL]{RESET} {msg}"); sys.exit(1)
def warn(msg): print(f"  {YELLOW}[WARN]{RESET} {msg}")
def section(title): print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")


# ─── 1. GEOBLOCK ─────────────────────────────────────────────────────────────

section("1. Geoblock")

try:
    resp = requests.get("https://polymarket.com/api/geoblock", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    print(f"  Resposta bruta: {json.dumps(data, indent=4)}")

    blocked = data.get("blocked")
    country = data.get("country", "?")
    ip      = data.get("ip", "?")

    if blocked:
        fail(f"IP bloqueado — país: {country}, IP: {ip}")
    else:
        ok(f"Não bloqueado — país: {country}, IP: {ip}")

except Exception as e:
    fail(f"Erro no geoblock: {e}")


# ─── 2. GAMMA API — /events ───────────────────────────────────────────────────

section("2. Gamma API — /events (Crypto, tag_id=21)")

GAMMA_BASE = "https://gamma-api.polymarket.com"

try:
    t0 = time.monotonic()
    resp = requests.get(
        f"{GAMMA_BASE}/events",
        params={
            "tag_id": 21,          # Crypto
            "closed": "false",
            "active": "true",
            "limit": 5,
            "offset": 0,
            "order": "volume24hr",
            "ascending": "false",
            "liquidity_min": 1000,
        },
        timeout=15,
    )
    latency_ms = (time.monotonic() - t0) * 1000
    resp.raise_for_status()
    events = resp.json()

    ok(f"Status 200 em {latency_ms:.0f}ms")
    ok(f"{len(events)} eventos recebidos")

    if not isinstance(events, list) or len(events) == 0:
        fail("Resposta não é uma lista ou está vazia")

    # Inspeciona o primeiro evento
    ev = events[0]
    print(f"\n  Primeiro evento:")
    print(f"    id       : {ev.get('id')}")
    print(f"    title    : {ev.get('title', '')[:70]}")
    print(f"    tags     : {[t.get('label') for t in (ev.get('tags') or [])]}")
    markets = ev.get("markets") or []
    print(f"    mercados : {len(markets)}")

    if markets:
        m = markets[0]
        prices_raw = m.get("outcomePrices", "[]")
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        token_ids_raw = m.get("clobTokenIds", "[]")
        token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw

        yes_price = float(prices[0]) if prices else None
        no_price  = float(prices[1]) if len(prices) > 1 else None
        yes_token = token_ids[0] if token_ids else None

        print(f"\n  Primeiro mercado:")
        print(f"    market_id  : {m.get('id')}")
        print(f"    question   : {m.get('question', '')[:70]}")
        print(f"    YES price  : {yes_price}")
        print(f"    NO price   : {no_price}")
        print(f"    yes_token  : {str(yes_token)[:20]}..." if yes_token else "    yes_token  : N/A")
        print(f"    liquidity  : ${float(m.get('liquidity') or 0):,.0f}")
        print(f"    end_date   : {m.get('endDate')}")

        ok("Campos obrigatórios presentes e parseáveis")
    else:
        warn("Evento sem mercados embutidos (pode ser normal)")

except Exception as e:
    fail(f"Erro na Gamma API: {e}")


# ─── 3. GAMMA API — penny markets (YES ≤ 4¢) ─────────────────────────────────

section("3. Gamma API - buscando penny markets (YES <= 4c)")

penny_count = 0
sample_penny = None

try:
    for tag_id in [21, 2, 120]:  # Crypto, Politics, Finance
        resp = requests.get(
            f"{GAMMA_BASE}/events",
            params={
                "tag_id": tag_id,
                "closed": "false",
                "active": "true",
                "limit": 100,
                "offset": 0,
                "order": "volume24hr",
                "ascending": "false",
                "liquidity_min": 1000,
            },
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()

        for ev in events:
            for m in (ev.get("markets") or []):
                prices_raw = m.get("outcomePrices", "[]")
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    yes_price = float(prices[0]) if prices else 1.0
                except Exception:
                    continue

                if yes_price <= 0.04 and float(m.get("liquidity") or 0) >= 1000:
                    penny_count += 1
                    # Prefere mercado com expiração futura (não hoje) para o teste de midpoint
                    end_date = m.get("endDate", "")
                    expires_today = end_date.startswith("2026-04-16")
                    if sample_penny is None and not expires_today:
                        sample_penny = (m, ev)

        time.sleep(0.1)

    ok(f"{penny_count} penny markets encontrados (YES ≤ 4¢, liquidez ≥ $1k)")

    if sample_penny:
        m, ev = sample_penny
        prices_raw = m.get("outcomePrices", "[]")
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        token_ids_raw = m.get("clobTokenIds", "[]")
        token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw

        print(f"\n  Exemplo penny market:")
        print(f"    question  : {m.get('question', '')[:70]}")
        print(f"    YES price : {float(prices[0]):.4f} (${float(prices[0])*100:.1f}¢)")
        print(f"    liquidity : ${float(m.get('liquidity') or 0):,.0f}")
        print(f"    end_date  : {m.get('endDate', '')[:10]}")

        # Guarda o yes_token para o próximo teste
        global_yes_token = str(token_ids[0]) if token_ids else None
    else:
        warn("Nenhum penny market encontrado nas 3 categorias testadas")
        global_yes_token = None

except Exception as e:
    fail(f"Erro buscando penny markets: {e}")


# ─── 4. CLOB API — /midpoint ─────────────────────────────────────────────────

section("4. CLOB API — /midpoint (token real do penny market)")

CLOB_BASE = "https://clob.polymarket.com"

if global_yes_token:
    try:
        t0 = time.monotonic()
        resp = requests.get(
            f"{CLOB_BASE}/midpoint",
            params={"token_id": global_yes_token},
            timeout=10,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()

        # API real retorna "mid", não "mid_price" como diz a documentação
        mid_raw = data.get("mid") or data.get("mid_price", "?")
        ok(f"Status 200 em {latency_ms:.0f}ms")
        ok(f"mid = '{mid_raw}' → {float(mid_raw):.4f}")

        # Coerência: midpoint deve ser próximo do YES price da Gamma
        mid_float = float(mid_raw)
        if 0 < mid_float <= 0.10:
            ok(f"Valor coerente para penny market ({mid_float:.4f})")
        else:
            warn(f"Valor fora do esperado para penny market: {mid_float:.4f}")

    except Exception as e:
        fail(f"Erro no CLOB /midpoint: {e}")
else:
    warn("Pulando teste de midpoint — nenhum token disponível")


# ─── 5. CLOB API — /midpoints (batch) ────────────────────────────────────────

section("5. CLOB API — /midpoints batch (3 tokens)")

# Usa um token_id conhecido publicamente (BTC market)
# Vamos pegar 3 tokens do penny market encontrado acima
batch_tokens = []
if global_yes_token:
    batch_tokens.append(global_yes_token)

# Tenta pegar mais 2 tokens de outros penny markets
if sample_penny:
    ev_markets = (sample_penny[1].get("markets") or [])
    for m in ev_markets[:3]:
        raw = m.get("clobTokenIds", "[]")
        try:
            tids = json.loads(raw) if isinstance(raw, str) else raw
            if tids and tids[0] not in batch_tokens:
                batch_tokens.append(str(tids[0]))
        except Exception:
            pass

batch_tokens = batch_tokens[:3]  # máximo 3 para o teste

if batch_tokens:
    try:
        t0 = time.monotonic()
        resp = requests.post(
            f"{CLOB_BASE}/midpoints",
            json=[{"token_id": tid} for tid in batch_tokens],
            timeout=10,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()

        ok(f"Status 200 em {latency_ms:.0f}ms")
        ok(f"{len(data)} preços retornados para {len(batch_tokens)} tokens")

        for tid, price in data.items():
            print(f"    {str(tid)[:20]}... → {price}")

    except Exception as e:
        fail(f"Erro no CLOB /midpoints batch: {e}")
else:
    warn("Pulando batch — sem tokens disponíveis")


# ─── Resultado final ──────────────────────────────────────────────────────────

print(f"\n{BOLD}{GREEN}=== Todos os testes passaram — API acessivel do Brasil ==={RESET}\n")
