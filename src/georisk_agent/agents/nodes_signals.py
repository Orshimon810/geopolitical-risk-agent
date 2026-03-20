import requests
from typing import Dict, Any

from georisk_agent.app.types import AgentState

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

# Countries where oil rents are a meaningful signal
OIL_PRODUCER_ISOS = {
    "SAU", "IRN", "IRQ", "ARE", "QAT", "KWT", "BHR", "OMN",
    "RUS", "NGA", "VEN", "NOR", "LBY", "DZA", "KAZ",
}


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


def signals_node(state: AgentState) -> AgentState:
    """
    Context-aware External Signals Agent.

    Determines relevant countries from the query and planner output,
    then fetches macroeconomic indicators only for those countries.
    Oil-producing countries also get Oil Rents (% of GDP).
    """
    query = state.get("query", "")
    plan = " ".join(state.get("plan", []))

    combined_text = f"{query} {plan}"
    countries = extract_relevant_countries(combined_text)

    signals = {
        "countries": {},
    }

    if not countries:
        signals["note"] = "No relevant countries detected from query."
        return {**state, "signals": signals}

    for iso in countries:
        entry: Dict[str, Any] = {}
        entry["trade_gdp"] = fetch_trade_gdp(iso)
        if iso in OIL_PRODUCER_ISOS:
            entry["oil_rents"] = fetch_oil_rents(iso)
        signals["countries"][iso] = entry

    return {**state, "signals": signals}


def extract_relevant_countries(text: str) -> list[str]:
    """
    Country extraction based on keyword matching.
    Handles both individual country names and region keywords.
    """
    text = text.lower()
    found = set()

    for name, iso in COUNTRY_NAME_TO_ISO.items():
        if name in text:
            found.add(iso)

    for region, isos in REGION_TO_ISOS.items():
        if region in text:
            found.update(isos)

    return list(found)

