from typing import Dict, Any, List


def _has_content(value: Any) -> bool:
    """
    Checks that a field is not empty or meaningless.
    Works for lists, dicts, and strings.
    """
    if value is None:
        return False

    if isinstance(value, (list, dict)):
        return len(value) > 0

    if isinstance(value, str):
        return len(value.strip()) > 20  # avoids shallow one-liners

    return True


def evaluate_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Production-style lightweight evaluator.

    Evaluates:
    - Structured output
    - Depth of analysis
    - Decision usefulness
    - Confidence sanity
    - Overconfidence risk
    """

    score = 0
    max_score = 10
    notes: List[str] = []

    risks = response.get("risks")
    signals = response.get("signals")
    impacts = response.get("market_impacts")
    scenarios = response.get("scenarios")
    confidence = response.get("confidence")

    # -------------------------
    # Core Intelligence Checks
    # -------------------------

    if _has_content(risks):
        score += 2
        if isinstance(risks, list) and len(risks) >= 2:
            score += 1  # depth bonus
        else:
            notes.append("Risk analysis is shallow")
    else:
        notes.append("Missing risk analysis")

    if _has_content(signals):
        score += 2
    else:
        notes.append("Missing external signals")

    if _has_content(impacts):
        score += 2
    else:
        notes.append("No market / asset-level insight")

    # -------------------------
    # Advanced Thinking Signals
    # -------------------------

    if _has_content(scenarios):
        score += 1
    else:
        notes.append("No scenario thinking detected")

    # -------------------------
    # Confidence Logic
    # -------------------------

    if _has_content(confidence):
        score += 1

        if isinstance(confidence, str):
            conf = confidence.lower()

            if "high" in conf and score < 6:
                notes.append("⚠️ Possible overconfidence")

            if "low" in conf and score >= 7:
                notes.append("Confidence may be too conservative")

    else:
        notes.append("Missing confidence score")

    # -------------------------
    # Decision Utility Check
    # -------------------------

    decision_keywords = [
        "monitor",
        "watch",
        "hedge",
        "prepare",
        "consider",
        "risk exposure",
        "allocation"
    ]

    response_text = str(response).lower()

    if any(word in response_text for word in decision_keywords):
        score += 1
    else:
        notes.append("Response lacks decision-oriented insight")

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
        "notes": notes
    }
