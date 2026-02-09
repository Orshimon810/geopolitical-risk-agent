import requests
from typing import Dict, Any

from georisk_agent.app.types import AgentState


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
    External Signals Agent

    Adds macroeconomic context without affecting core analysis.
    """
    signals = {}

    us_trade = fetch_trade_gdp("USA")
    china_trade = fetch_trade_gdp("CHN")

    signals["trade_openness"] = {
        "us": us_trade,
        "china": china_trade,
        "indicator": "Trade (% of GDP)",
    }

    return {
        **state,
        "signals": signals,
    }
