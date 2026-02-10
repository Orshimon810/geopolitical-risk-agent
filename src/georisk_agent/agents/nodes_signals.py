import requests
from typing import Dict, Any

from georisk_agent.app.types import AgentState

COUNTRY_NAME_TO_ISO = {
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
}


WORLD_BANK_API = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json"


def fetch_trade_gdp(country: str = "USA") -> Dict[str, Any]:
    """
    Fetch Trade (% of GDP) from World Bank API.
    Indicator: NE.TRD.GNFS.ZS
    """
    url = WORLD_BANK_API.format(
        country=country,
        indicator="NE.TRD.GNFS.ZS",
    )

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
        return {
            "status": "error",
            "error": str(e),
            "source": "World Bank",
        }


def signals_node(state: AgentState) -> AgentState:
    """
    Context-aware External Signals Agent.

    Determines relevant countries from the query and planner output,
    then fetches macroeconomic indicators only for those countries.
    """
    query = state.get("query", "")
    plan = " ".join(state.get("plan", []))

    combined_text = f"{query} {plan}"
    countries = extract_relevant_countries(combined_text)

    signals = {
        "indicator": "Trade (% of GDP)",
        "countries": {},
    }

    if not countries:
        signals["note"] = "No relevant countries detected from query."
        return {
            **state,
            "signals": signals,
        }

    for iso in countries:
        signals["countries"][iso] = fetch_trade_gdp(iso)

    return {
        **state,
        "signals": signals,
    }


def extract_relevant_countries(text: str) -> list[str]:
    """
    Naive country extraction based on keyword matching.
    Suitable for controlled signals enrichment.
    """
    text = text.lower()
    found = set()

    for name, iso in COUNTRY_NAME_TO_ISO.items():
        if name in text:
            found.add(iso)

    return list(found)

