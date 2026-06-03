from typing import Dict, Any, List


# ---------------------------------------------------------------------------
# Quality keyword sets — scenario-agnostic financial market language.
# These describe mechanics (transmission, mispricing, timelines, asset classes)
# not specific countries, regions, or tickers.
# ---------------------------------------------------------------------------

_FIRST_MOVER_KEYWORDS = {
    "first", "before", "ahead", "initially", "immediately", "prior",
    "transmission", "contagion", "channel", "spillover", "ripple",
    "reprices", "repricing", "selloff", "sell-off",
    "flight to", "safe haven", "risk-off", "risk off",
    "equities", "bonds", "commodities", "currencies", "credit",
    "spreads", "yields", "volatility", "rates", "fx",
}

_MISPRICING_KEYWORDS = {
    "underpriced", "underestimate", "mispriced", "may be wrong",
    "incorrect assumption", "consensus", "market assumes",
    "market believes", "market expects", "market is pricing",
    "not pricing", "overlooking", "discounting", "ignoring",
    "asymmetric", "tail risk", "complacency",
}

_TIMELINE_KEYWORDS = {
    "month", "week", "year", "quarter", "day",
    "short-term", "medium-term", "long-term",
    "within", "over the next", "near-term",
}

_ACTION_VERBS = {
    "reduce", "increase", "hedge", "rotate", "avoid", "add",
    "buy", "sell", "allocate", "shift", "exit", "enter",
    "underweight", "overweight", "diversify", "position",
}

_SPECIFIC_ASSET_CLASSES = {
    "equities", "bonds", "commodities", "currencies", "credit",
    "spreads", "duration", "defensives", "cyclicals", "energy",
    "emerging", "sovereign", "corporate", "inflation", "real assets",
    "cash", "gold", "oil", "rates", "volatility",
}


# ---------------------------------------------------------------------------
# Quality helpers
# ---------------------------------------------------------------------------

def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict)):
        return len(value) > 0
    if isinstance(value, str):
        return len(value.strip()) > 5
    return True


def _has_first_mover_language(impacts: list) -> bool:
    combined = " ".join(impacts).lower()
    return any(kw in combined for kw in _FIRST_MOVER_KEYWORDS)


def _has_mispricing_language(risks: list) -> bool:
    combined = " ".join(risks).lower()
    return any(kw in combined for kw in _MISPRICING_KEYWORDS)


def _has_timeline_language(scenarios: list) -> bool:
    return all(
        any(kw in s.lower() for kw in _TIMELINE_KEYWORDS)
        for s in scenarios
    )


def _has_specific_takeaway(takeaway: list) -> bool:
    combined = " ".join(takeaway).lower()
    has_action = any(v in combined for v in _ACTION_VERBS)
    has_specific_asset = any(a in combined for a in _SPECIFIC_ASSET_CLASSES)
    return has_action and has_specific_asset


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

def evaluate_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Quality-aware evaluator (0–10).

    Each section rewards both presence/count AND substantive content:
    - Market impacts: must name first-mover assets or transmission channels
    - Risks: must use market mispricing / asymmetric-expectation language
    - Scenarios: must contain explicit timelines in both cases
    - Takeaway: must include an action verb + a specific asset class
    """

    score = 0
    max_score = 10
    notes: List[str] = []

    market_impacts = response.get("market_impacts")
    risks = response.get("risks")
    scenarios = response.get("scenarios")
    signals = response.get("signals")
    confidence = response.get("confidence")
    takeaway = response.get("investor_takeaway")

    # -------------------------
    # Market Impacts (0–3)
    # -------------------------

    if _has_content(market_impacts):
        score += 1
        if isinstance(market_impacts, list) and len(market_impacts) >= 2:
            score += 1
        else:
            notes.append("Market impacts lack depth")
        if isinstance(market_impacts, list) and _has_first_mover_language(market_impacts):
            score += 1
        else:
            notes.append("Market impacts missing first-mover or transmission language")
    else:
        notes.append("Missing market impacts")

    # -------------------------
    # Risk Analysis (0–2)
    # -------------------------

    if isinstance(risks, list) and len(risks) >= 2:
        score += 1
        if _has_mispricing_language(risks):
            score += 1
        else:
            notes.append("Risks missing market mispricing language")
    elif _has_content(risks):
        score += 1
        notes.append("Risk analysis is shallow (fewer than 2 risks)")
    else:
        notes.append("Missing risk analysis")

    # -------------------------
    # External Signals (0–1)
    # -------------------------

    if _has_content(signals):
        score += 1
    else:
        notes.append("Missing external signals")

    # -------------------------
    # Scenario Reasoning (0–2)
    # -------------------------

    if isinstance(scenarios, list) and len(scenarios) >= 2:
        score += 1
        if _has_timeline_language(scenarios):
            score += 1
        else:
            notes.append("Scenarios missing explicit timelines")
    else:
        notes.append("Insufficient scenario thinking")

    # -------------------------
    # Decision Utility (0–1)
    # -------------------------

    if isinstance(takeaway, list) and _has_content(takeaway) and _has_specific_takeaway(takeaway):
        score += 1
    elif _has_content(takeaway):
        notes.append("Investor takeaway too generic — needs action verb and specific asset class")
    else:
        notes.append("Missing investor takeaway")

    # -------------------------
    # Confidence Calibration (0–1)
    # -------------------------

    if _has_content(confidence):
        score += 1

        if isinstance(confidence, str):
            conf = confidence.lower()

            if conf == "high" and score < 7:
                notes.append("⚠️ Confidence may be overstated relative to evidence")

            if conf == "low" and score >= 8:
                notes.append("Confidence may be overly conservative")
    else:
        notes.append("Missing confidence score")

    # -------------------------
    # Realism Guardrail
    # -------------------------

    if score >= 9:
        depth_ok = (
            isinstance(market_impacts, list) and len(market_impacts) >= 2
            and isinstance(risks, list) and len(risks) >= 2
            and isinstance(scenarios, list) and len(scenarios) >= 2
        )

        if not depth_ok:
            score -= 1
            notes.append("Score capped due to limited analytical depth")

    # -------------------------
    # Final Rating
    # -------------------------

    if score >= 8:
        rating = "STRONG"
    elif score >= 5:
        rating = "MODERATE"
    else:
        rating = "WEAK"

    return {
        "score": score,
        "max_score": max_score,
        "rating": rating,
        "notes": notes,
    }
