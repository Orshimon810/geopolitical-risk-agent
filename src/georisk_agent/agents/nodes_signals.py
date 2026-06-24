import logging
import re
import requests
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any

from georisk_agent.app.types import DynamicAgentState

logger = logging.getLogger(__name__)


def _build_country_map() -> dict[str, str]:
    """
    Build a comprehensive country name → ISO 3166-1 alpha-3 mapping.

    Uses pycountry for full coverage of all 249 countries (official names,
    common names). Supplemented by short-form aliases (informal names and
    abbreviations that don't appear in the ISO standard).

    Alpha-2 codes (2-letter) are intentionally excluded from auto-generation
    to avoid false positives — e.g. "in" matching India in normal English text.
    Only unambiguous short aliases are added manually.
    """
    mapping: dict[str, str] = {}

    try:
        import pycountry
        for c in pycountry.countries:
            a3 = c.alpha_3
            mapping[c.name.lower()] = a3
            if hasattr(c, "common_name"):
                mapping[c.common_name.lower()] = a3
            if hasattr(c, "official_name"):
                mapping[c.official_name.lower()] = a3
    except ImportError:
        logger.warning("pycountry not installed — country detection limited to manual aliases")

    # Short-form aliases and informal names not in the ISO standard
    mapping.update({
        "russia":  "RUS",
        "uk":      "GBR",
        "uae":     "ARE",
        "us":      "USA",
        "usa":     "USA",
        "korea":   "KOR",   # default to South Korea
        "turkiye": "TUR",
    })
    return mapping


COUNTRY_NAME_TO_ISO: dict[str, str] = _build_country_map()


REGION_TO_ISOS = {
    # Existing regions
    "middle east":      ["SAU", "IRN", "IRQ", "ARE", "QAT"],
    "gulf":             ["SAU", "ARE", "QAT", "KWT", "BHR", "OMN"],
    "gcc":              ["SAU", "ARE", "QAT", "KWT", "BHR", "OMN"],
    "opec":             ["SAU", "IRN", "IRQ", "ARE", "KWT", "NGA", "VEN"],
    "eastern europe":   ["UKR", "RUS", "POL"],
    "southeast asia":   ["IDN", "MYS", "THA", "VNM", "PHL"],
    "asean":            ["IDN", "MYS", "THA", "VNM", "PHL", "SGP", "MMR"],
    "latin america":    ["BRA", "MEX", "VEN", "COL", "ARG", "CHL", "PER"],
    "latam":            ["BRA", "MEX", "VEN", "COL", "ARG", "CHL", "PER"],
    "africa":           ["NGA", "ZAF", "EGY"],
    # Extended blocs
    "global south":     ["BRA", "IND", "IDN", "ZAF", "MEX", "ARG", "NGA", "EGY"],
    "brics":            ["BRA", "RUS", "IND", "CHN", "ZAF", "EGY", "IRN", "SAU", "ARE"],
    "emerging markets": ["BRA", "CHN", "IND", "IDN", "MEX", "ZAF", "TUR", "POL", "THA"],
    "em":               ["BRA", "CHN", "IND", "IDN", "MEX", "ZAF", "TUR", "POL", "THA"],
    "sub-saharan africa": ["NGA", "ZAF", "ETH", "KEN", "GHA", "TZA"],
    "sub saharan africa": ["NGA", "ZAF", "ETH", "KEN", "GHA", "TZA"],
    "south asia":       ["IND", "PAK", "BGD", "LKA"],
    "sahel":            ["MLI", "NER", "BFA", "TCD", "MRT"],
    "balkans":          ["SRB", "HRV", "BIH", "MKD", "ALB", "MNE"],
    "western europe":   ["DEU", "FRA", "GBR", "ITA", "ESP", "NLD"],
    "nato":             ["USA", "GBR", "DEU", "FRA", "POL", "TUR"],
    "central asia":     ["KAZ", "UZB", "TKM", "KGZ", "TJK"],
}

# ISOs that belong to the Global North / developed world.
# Used by the scope-mismatch guard to detect leakage when the query is
# about emerging markets or the Global South.
_GLOBAL_NORTH_ISOS = frozenset({
    "USA", "GBR", "DEU", "FRA", "JPN", "CAN", "AUS", "NLD", "BEL",
    "SWE", "NOR", "DNK", "FIN", "CHE", "AUT", "IRL", "NZL", "SGP",
    "ISR", "KOR",
})

# Tokens that signal the query is EM/Global-South scoped.
_EM_SCOPE_TOKENS = frozenset({
    "global south", "emerging markets", "emerging market", "em bonds",
    "em bond", "em equity", "em equities", "brics", "frontier markets",
    "frontier market", "developing world", "developing countries",
    "developing economies",
})

WORLD_BANK_API = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json"

OIL_PRODUCER_ISOS = {
    "SAU", "IRN", "IRQ", "ARE", "QAT", "KWT", "BHR", "OMN",
    "RUS", "NGA", "VEN", "NOR", "LBY", "DZA", "KAZ",
}

# -------------------------
# Thematic indicator config
# -------------------------

# Keyword sets that identify a query theme
_THEME_KEYWORDS: Dict[str, frozenset] = {
    "energy":     frozenset({"oil", "gas", "energy", "opec", "brent", "lng", "pipeline", "fuel", "petroleum"}),
    "sovereign_debt": frozenset({"bond", "bonds", "debt", "sovereign", "yield", "yields", "credit rating", "default", "em bond", "em bonds"}),
    "fx":         frozenset({"dollar", "usd", "currency", "fx", "forex", "exchange rate", "devaluation", "reserves", "em bond", "em bonds"}),
    "trade":      frozenset({"trade", "tariff", "tariffs", "export", "imports", "wto", "supply chain", "port", "shipping"}),
    "defense":    frozenset({"defense", "defence", "nato", "military", "arms", "weapon", "warfare", "sanctions"}),
    "semiconductors": frozenset({"semiconductor", "chip", "chips", "asml", "tsmc", "nvidia", "foundry", "lithography", "export control"}),
}

# Extra World Bank indicators unlocked per theme (beyond trade_gdp default)
_THEME_WB_INDICATORS: Dict[str, Dict[str, str]] = {
    "sovereign_debt": {
        "GC.DOD.TOTL.GD.ZS": "govt_debt_pct_gdp",
        "FR.INR.RINR": "real_interest_rate",
    },
    "fx": {
        "FI.RES.TOTL.CD": "fx_reserves_usd",
    },
    "defense": {
        "MS.MIL.XPND.GD.ZS": "military_spend_pct_gdp",
    },
}

# Extra market tickers unlocked per theme
_THEME_TICKERS: Dict[str, Dict[str, str]] = {
    "energy":     {"NG=F": "Natural Gas ($/MMBtu)", "XLE": "Energy Select ETF"},
    "sovereign_debt": {"EMB": "EM Bond ETF (USD)", "HYG": "High-Yield Bond ETF"},
    "fx":         {"EEM": "EM Equity ETF", "EMB": "EM Bond ETF (USD)", "UUP": "USD Bullish ETF"},
    "defense":    {"ITA": "iShares US Aerospace & Defense ETF", "XAR": "SPDR Defense ETF"},
    "semiconductors": {"SOXX": "iShares Semiconductor ETF", "TSM": "TSMC", "NVDA": "Nvidia"},
}

# -------------------------
# Market data config
# -------------------------

CORE_TICKERS: Dict[str, str] = {
    "^VIX":     "VIX (Market Fear)",
    "BZ=F":     "Brent Crude ($/bbl)",
    "GC=F":     "Gold ($/oz)",
    "DX-Y.NYB": "US Dollar Index",
}

COUNTRY_TICKERS: Dict[str, Dict[str, str]] = {
    "SAU": {"NG=F": "Natural Gas ($/MMBtu)"},
    "IRN": {"NG=F": "Natural Gas ($/MMBtu)"},
    "IRQ": {"NG=F": "Natural Gas ($/MMBtu)"},
    "ARE": {"NG=F": "Natural Gas ($/MMBtu)"},
    "KWT": {"NG=F": "Natural Gas ($/MMBtu)"},
    "RUS": {"NG=F": "Natural Gas ($/MMBtu)"},
    "UKR": {"NG=F": "Natural Gas ($/MMBtu)"},
    "CHN": {"FXI": "China Large-Cap ETF", "TSM": "TSMC"},
    "TWN": {"FXI": "China Large-Cap ETF", "TSM": "TSMC"},
    "BRA": {"EEM": "EM ETF"},
    "IND": {"EEM": "EM ETF"},
    "TUR": {"EEM": "EM ETF"},
    "DEU": {"FEZ": "Euro Stoxx 50 ETF"},
    "FRA": {"FEZ": "Euro Stoxx 50 ETF"},
    "GBR": {"FEZ": "Euro Stoxx 50 ETF"},
}


def detect_themes(text: str) -> list[str]:
    """Return the list of query themes detected from keyword matching."""
    text_lower = text.lower()
    return [
        theme for theme, keywords in _THEME_KEYWORDS.items()
        if any(kw in text_lower for kw in keywords)
    ]


def build_tickers(isos: list[str], themes: list[str] | None = None) -> Dict[str, str]:
    """Build the full ticker dict for detected countries + themes, always including core tickers."""
    tickers = dict(CORE_TICKERS)
    for iso in isos:
        tickers.update(COUNTRY_TICKERS.get(iso, {}))
    for theme in (themes or []):
        tickers.update(_THEME_TICKERS.get(theme, {}))
    return tickers


def fetch_market_snapshot(tickers: Dict[str, str]) -> Dict[str, Any]:
    def _fetch_one(symbol: str, label: str):
        try:
            info = yf.Ticker(symbol).fast_info
            price = info.last_price
            prev = info.previous_close
            change_pct = ((price - prev) / prev * 100) if prev else None
            return symbol, {
                "label": label,
                "price": round(price, 2),
                "change_1d_pct": round(change_pct, 2) if change_pct is not None else None,
                "status": "ok",
            }
        except Exception as e:
            return symbol, {"label": label, "status": "error", "error": str(e)}

    results: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as executor:
        futures = {executor.submit(_fetch_one, sym, lbl): sym for sym, lbl in tickers.items()}
        for future in as_completed(futures):
            symbol, result = future.result()
            results[symbol] = result
    return results


# -------------------------
# World Bank helpers
# -------------------------

def _fetch_indicator(country: str, indicator: str) -> Dict[str, Any]:
    url = WORLD_BANK_API.format(country=country, indicator=indicator)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data or len(data) < 2 or not data[1]:
            return {"status": "no_data"}

        latest = next((d for d in data[1] if d.get("value") is not None), None)
        if not latest:
            return {"status": "no_data"}

        return {
            "status": "ok",
            "year": latest.get("date"),
            "value": latest.get("value"),
            "source": "World Bank",
        }

    except Exception as e:
        return {"status": "error", "error": str(e), "source": "World Bank"}


def fetch_trade_gdp(country: str) -> Dict[str, Any]:
    return _fetch_indicator(country, "NE.TRD.GNFS.ZS")


def fetch_oil_rents(country: str) -> Dict[str, Any]:
    return _fetch_indicator(country, "NY.GDP.PETR.RT.ZS")


# -------------------------
# Signals Node
# -------------------------

def fetch_portfolio_prices(holdings: list[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Fetch current price and 1-day % change for each portfolio holding.
    Errors are caught per-ticker and stored as {"status": "error"}.
    Returns a dict keyed by ticker symbol.
    """
    if not holdings:
        return {}

    tickers: Dict[str, str] = {
        h["ticker"]: h.get("name", h["ticker"])
        for h in holdings
        if h.get("ticker")
    }

    def _fetch_one(symbol: str, label: str):
        try:
            info = yf.Ticker(symbol).fast_info
            price = info.last_price
            prev = info.previous_close
            change_pct = ((price - prev) / prev * 100) if prev else None
            return symbol, {
                "label": label,
                "price": round(price, 2),
                "change_1d_pct": round(change_pct, 2) if change_pct is not None else None,
                "status": "ok",
            }
        except Exception as e:
            return symbol, {"label": label, "status": "error", "error": str(e)}

    results: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as executor:
        futures = {executor.submit(_fetch_one, sym, lbl): sym for sym, lbl in tickers.items()}
        for future in as_completed(futures):
            symbol, result = future.result()
            results[symbol] = result
    return results


def signals_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    Context-aware External Signals Agent.

    Detects relevant countries and query themes, fetches World Bank macro
    indicators (base + theme-specific), and fetches live Yahoo Finance market
    prices (always core + country-specific + theme-specific).
    When state["portfolio"] is set, also fetches prices for portfolio tickers.
    """
    query = state.get("query", "")
    plan = " ".join(state.get("plan", []))
    portfolio = state.get("portfolio")

    combined_text = f"{query} {plan}"
    countries = extract_relevant_countries(combined_text)
    themes = detect_themes(combined_text)

    if themes:
        logger.info("signals_node: detected themes=%s", themes)

    signals: Dict[str, Any] = {"countries": {}}

    def _fetch_country(iso: str):
        entry: Dict[str, Any] = {"trade_gdp": fetch_trade_gdp(iso)}
        if iso in OIL_PRODUCER_ISOS:
            entry["oil_rents"] = fetch_oil_rents(iso)
        # Pull theme-specific World Bank indicators
        for theme in themes:
            for indicator_code, field_name in _THEME_WB_INDICATORS.get(theme, {}).items():
                if field_name not in entry:
                    entry[field_name] = _fetch_indicator(iso, indicator_code)
        return iso, entry

    if not countries:
        signals["note"] = "No relevant countries detected from query."
    else:
        with ThreadPoolExecutor(max_workers=min(len(countries), 6)) as executor:
            futures = [executor.submit(_fetch_country, iso) for iso in countries]
            for future in as_completed(futures):
                iso, entry = future.result()
                signals["countries"][iso] = entry

    tickers = build_tickers(countries, themes)
    signals["market_data"] = fetch_market_snapshot(tickers)

    if portfolio:
        signals["portfolio_prices"] = fetch_portfolio_prices(portfolio)

    return {**state, "signals": signals}


def extract_relevant_countries(text: str) -> list[str]:
    """
    Country extraction based on keyword matching with scope-mismatch guard.

    Handles both individual country names and region keywords. When the query
    is clearly EM/Global-South scoped but only Global North ISOs are detected
    (leaked in from incidental plan text), the Global North entries are dropped.
    """
    text_lower = text.lower()
    found = set()

    for name, iso in COUNTRY_NAME_TO_ISO.items():
        if re.search(r'\b' + re.escape(name) + r'\b', text_lower):
            found.add(iso)

    for region, isos in REGION_TO_ISOS.items():
        if re.search(r'\b' + re.escape(region) + r'\b', text_lower):
            found.update(isos)

    # Scope-mismatch guard: if the query is EM/Global-South scoped but all
    # detected ISOs are Global North, drop the Global North leakage so we don't
    # return misleading indicators (e.g. USA trade stats for an EM bonds query).
    is_em_scoped = any(token in text_lower for token in _EM_SCOPE_TOKENS)
    if is_em_scoped and found and found.issubset(_GLOBAL_NORTH_ISOS):
        logger.info(
            "signals_node: scope-mismatch guard dropped Global North ISOs %s "
            "— query is EM-scoped but no EM countries matched explicitly",
            found,
        )
        found = set()

    return list(found)
