import logging
import re
import requests
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any

from georisk_agent.app.types import DynamicAgentState

logger = logging.getLogger(__name__)

COUNTRY_NAME_TO_ISO = {
    # Major economies
    "united states": "USA",
    "us": "USA",
    "usa": "USA",
    "china": "CHN",
    "germany": "DEU",
    "russia": "RUS",
    "india": "IND",
    "japan": "JPN",
    "south korea": "KOR",
    "korea": "KOR",
    "france": "FRA",
    "united kingdom": "GBR",
    "uk": "GBR",
    "brazil": "BRA",
    "canada": "CAN",
    "australia": "AUS",
    "italy": "ITA",
    "mexico": "MEX",
    "indonesia": "IDN",
    "turkey": "TUR",
    "turkiye": "TUR",
    # Middle East
    "saudi arabia": "SAU",
    "iran": "IRN",
    "iraq": "IRQ",
    "israel": "ISR",
    "uae": "ARE",
    "united arab emirates": "ARE",
    "qatar": "QAT",
    "kuwait": "KWT",
    "bahrain": "BHR",
    "oman": "OMN",
    "jordan": "JOR",
    "lebanon": "LBN",
    "yemen": "YEM",
    "syria": "SYR",
    # Africa & other
    "egypt": "EGY",
    "nigeria": "NGA",
    "south africa": "ZAF",
    "venezuela": "VEN",
    "ukraine": "UKR",
    "pakistan": "PAK",
    "taiwan": "TWN",
}


REGION_TO_ISOS = {
    "middle east": ["SAU", "IRN", "IRQ", "ARE", "QAT"],
    "gulf": ["SAU", "ARE", "QAT", "KWT", "BHR", "OMN"],
    "opec": ["SAU", "IRN", "IRQ", "ARE", "KWT", "NGA", "VEN"],
    "eastern europe": ["UKR", "RUS", "POL"],
    "southeast asia": ["IDN", "MYS", "THA", "VNM"],
    "latin america": ["BRA", "MEX", "VEN", "COL"],
    "africa": ["NGA", "ZAF", "EGY"],
}

WORLD_BANK_API = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json"

OIL_PRODUCER_ISOS = {
    "SAU", "IRN", "IRQ", "ARE", "QAT", "KWT", "BHR", "OMN",
    "RUS", "NGA", "VEN", "NOR", "LBY", "DZA", "KAZ",
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


def build_tickers(isos: list[str]) -> Dict[str, str]:
    """Build the full ticker dict for detected countries, always including core tickers."""
    tickers = dict(CORE_TICKERS)
    for iso in isos:
        tickers.update(COUNTRY_TICKERS.get(iso, {}))
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

    Detects relevant countries, fetches World Bank macro indicators,
    and fetches live Yahoo Finance market prices (always core + query-specific).
    When state["portfolio"] is set, also fetches prices for portfolio tickers.
    """
    query = state.get("query", "")
    plan = " ".join(state.get("plan", []))
    portfolio = state.get("portfolio")

    combined_text = f"{query} {plan}"
    countries = extract_relevant_countries(combined_text)

    signals: Dict[str, Any] = {"countries": {}}

    def _fetch_country(iso: str):
        entry: Dict[str, Any] = {"trade_gdp": fetch_trade_gdp(iso)}
        if iso in OIL_PRODUCER_ISOS:
            entry["oil_rents"] = fetch_oil_rents(iso)
        return iso, entry

    if not countries:
        signals["note"] = "No relevant countries detected from query."
    else:
        with ThreadPoolExecutor(max_workers=min(len(countries), 6)) as executor:
            futures = [executor.submit(_fetch_country, iso) for iso in countries]
            for future in as_completed(futures):
                iso, entry = future.result()
                signals["countries"][iso] = entry

    tickers = build_tickers(countries)
    signals["market_data"] = fetch_market_snapshot(tickers)

    if portfolio:
        signals["portfolio_prices"] = fetch_portfolio_prices(portfolio)

    return {**state, "signals": signals}


def extract_relevant_countries(text: str) -> list[str]:
    """
    Country extraction based on keyword matching.
    Handles both individual country names and region keywords.
    """
    text = text.lower()
    found = set()

    for name, iso in COUNTRY_NAME_TO_ISO.items():
        if re.search(r'\b' + re.escape(name) + r'\b', text):
            found.add(iso)

    for region, isos in REGION_TO_ISOS.items():
        if re.search(r'\b' + re.escape(region) + r'\b', text):
            found.update(isos)

    return list(found)
