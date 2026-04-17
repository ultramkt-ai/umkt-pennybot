"""
test_filters_live.py — Filtros rodando sobre mercados REAIS da Polymarket.

Busca mercados via Gamma API, normaliza, classifica por categoria e roda
o pipeline completo para PENNY_STRATEGY e NO_SYSTEMATIC_STRATEGY.

Não persiste nada, não abre posição, não precisa de auth.
"""

import sys
import time
from collections import Counter

sys.path.insert(0, ".")

from config import PENNY_STRATEGY, NO_SYSTEMATIC_STRATEGY, POLYMARKET_TAGS
from gamma_client import fetch_events_by_tag, normalize_market, GammaAPIError
from filters import filter_markets, FilterSummary

# ─── Helpers de output ───────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")

def ok(msg: str)   -> None: print(f"  {GREEN}[OK]{RESET}   {msg}")
def warn(msg: str) -> None: print(f"  {YELLOW}[WARN]{RESET} {msg}")
def info(msg: str) -> None: print(f"  {msg}")


# ─── 1. Coleta de mercados reais ─────────────────────────────────────────────

section("1. Buscando mercados reais (Gamma API)")

# Busca todas as categorias configuradas, com filtro server-side de liquidez
raw_markets: list[dict] = []
fetch_errors: list[str] = []

for category, tag_id in POLYMARKET_TAGS.items():
    try:
        count_before = len(raw_markets)
        for event in fetch_events_by_tag(
            tag_id,
            liquidity_min=PENNY_STRATEGY.min_liquidity,
            max_pages=5,
        ):
            for raw_market in (event.get("markets") or []):
                normalized = normalize_market(raw_market, parent_event=event)
                normalized["category"] = category
                raw_markets.append(normalized)
        fetched = len(raw_markets) - count_before
        ok(f"{category:15s} (tag {tag_id:>6}) → {fetched:>4} mercados")
    except GammaAPIError as e:
        warn(f"{category}: erro na API — {e}")
        fetch_errors.append(category)
    time.sleep(0.15)  # throttle entre categorias

info("")
info(f"Total coletado: {len(raw_markets)} mercados em {len(POLYMARKET_TAGS) - len(fetch_errors)} categorias")
if fetch_errors:
    warn(f"Falhou: {fetch_errors}")

if not raw_markets:
    print(f"\n{RED}Nenhum mercado coletado — verifique a conexão.{RESET}")
    sys.exit(1)


# ─── 2. PENNY_STRATEGY ───────────────────────────────────────────────────────

section("2. Pipeline: PENNY_STRATEGY (YES <= $0.04)")

eligible_penny, summaries_penny = filter_markets(raw_markets, PENNY_STRATEGY)

rejected_penny = [s for s in summaries_penny if not s.passed]
rejection_counts: Counter = Counter(s.failed_at for s in rejected_penny)

info(f"Mercados analisados : {len(raw_markets)}")
info(f"Elegíveis           : {len(eligible_penny)}")
info(f"Rejeitados          : {len(rejected_penny)}")
info("")
info("Motivos de rejeição:")
for filter_name, count in rejection_counts.most_common():
    pct = count / len(raw_markets) * 100
    info(f"  {filter_name:<25} {count:>5}  ({pct:.1f}%)")

if eligible_penny:
    info("")
    info(f"Top {min(10, len(eligible_penny))} mercados elegíveis (penny):")
    for m in eligible_penny[:10]:
        info(
            f"  [{m['category']:>12}] "
            f"YES={m['yes_price']:.3f}  "
            f"liq=${m['liquidity']:>9,.0f}  "
            f"{m['question'][:65]}"
        )
else:
    warn("Nenhum mercado elegível para PENNY_STRATEGY nos dados atuais.")


# ─── 3. NO_SYSTEMATIC_STRATEGY ───────────────────────────────────────────────

section("3. Pipeline: NO_SYSTEMATIC_STRATEGY (NO <= $0.50)")

eligible_no, summaries_no = filter_markets(raw_markets, NO_SYSTEMATIC_STRATEGY)

rejected_no = [s for s in summaries_no if not s.passed]
rejection_counts_no: Counter = Counter(s.failed_at for s in rejected_no)

info(f"Mercados analisados : {len(raw_markets)}")
info(f"Elegíveis           : {len(eligible_no)}")
info(f"Rejeitados          : {len(rejected_no)}")
info("")
info("Motivos de rejeição:")
for filter_name, count in rejection_counts_no.most_common():
    pct = count / len(raw_markets) * 100
    info(f"  {filter_name:<25} {count:>5}  ({pct:.1f}%)")

if eligible_no:
    info("")
    info(f"Top {min(10, len(eligible_no))} mercados elegíveis (NO sistemático):")
    for m in eligible_no[:10]:
        info(
            f"  [{m['category']:>12}] "
            f"NO={m['no_price']:.3f}  "
            f"liq=${m['liquidity']:>9,.0f}  "
            f"{m['question'][:65]}"
        )
else:
    warn("Nenhum mercado elegível para NO_SYSTEMATIC_STRATEGY nos dados atuais.")


# ─── 4. Sanidade dos dados normalizados ──────────────────────────────────────

section("4. Sanidade dos dados normalizados")

no_end_date   = sum(1 for m in raw_markets if not m.get("end_date"))
no_yes_token  = sum(1 for m in raw_markets if not m.get("yes_token_id"))
price_sum_off = [
    m for m in raw_markets
    if m.get("yes_price", 0) > 0 and m.get("no_price", 0) > 0
    and not (0.95 <= m["yes_price"] + m["no_price"] <= 1.05)
]

info(f"Mercados sem end_date      : {no_end_date}")
info(f"Mercados sem yes_token_id  : {no_yes_token}")
info(f"Mercados com soma Y+N fora de [0.95, 1.05]: {len(price_sum_off)}")

if price_sum_off:
    warn("Exemplos com soma anômala:")
    for m in price_sum_off[:3]:
        info(f"  YES={m['yes_price']:.3f} + NO={m['no_price']:.3f} = {m['yes_price']+m['no_price']:.3f}  {m['question'][:60]}")

if no_end_date == 0 and no_yes_token == 0 and len(price_sum_off) == 0:
    ok("Todos os campos críticos presentes e coerentes.")


# ─── Resumo final ─────────────────────────────────────────────────────────────

section("Resumo")
info(f"Mercados coletados     : {len(raw_markets)}")
info(f"Elegíveis penny        : {len(eligible_penny)}  ({len(eligible_penny)/len(raw_markets)*100:.1f}%)")
info(f"Elegíveis NO sist.     : {len(eligible_no)}  ({len(eligible_no)/len(raw_markets)*100:.1f}%)")
print()
