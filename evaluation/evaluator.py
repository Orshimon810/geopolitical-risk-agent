from typing import Dict, Any, List


def _has_content(value: Any) -> bool:
    """
    Checks that a field is not empty or trivial.
    """
    if value is None:
        return False

    if isinstance(value, (list, dict)):
        return len(value) > 0

    if isinstance(value, str):
        return len(value.strip()) > 5

    return True


def evaluate_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight production-style evaluator.

    Evaluation philosophy:
    - Reward structured, decision-oriented analysis
    - Penalize shallow, generic, or overconfident outputs
    - Avoid perfect scores unless analysis is truly robust
    """

    score = 0
    max_score = 10
    notes: List[str] = []

    # Extract fields
    market_impacts = response.get("market_impacts")
    risks = response.get("risks")
    scenarios = response.get("scenarios")
    signals = response.get("signals")
    confidence = response.get("confidence")
    takeaway = response.get("investor_takeaway")

    # -------------------------
    # Core Market Intelligence
    # -------------------------

    if _has_content(market_impacts):
        score += 2
        if isinstance(market_impacts, list) and len(market_impacts) >= 2:
            score += 1
        else:
            notes.append("Market impacts lack depth")
    else:
        notes.append("Missing market impacts")

    # -------------------------
    # Risk Analysis
    # -------------------------

    if _has_content(risks):
        score += 1
        if isinstance(risks, list) and len(risks) >= 2:
            score += 1
        else:
            notes.append("Risk analysis is shallow")
    else:
        notes.append("Missing risk analysis")

    # -------------------------
    # External Signals
    # -------------------------

    if _has_content(signals):
        score += 1
    else:
        notes.append("Missing external signals")

    # -------------------------
    # Scenario Reasoning
    # -------------------------

    if isinstance(scenarios, list) and len(scenarios) >= 2:
        score += 2
    else:
        notes.append("Insufficient scenario thinking")

    # -------------------------
    # Decision Utility
    # -------------------------

    if _has_content(takeaway):
        score += 1
    else:
        notes.append("Missing investor takeaway")

    # -------------------------
    # Confidence Calibration
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
    # Prevent inflated scores when depth is limited

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
