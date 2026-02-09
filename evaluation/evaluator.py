from typing import Dict, Any


def evaluate_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight heuristic evaluation.
    Designed for portfolio projects — not academic scoring.
    """

    report_text = str(response).lower()

    score = 0
    notes = []

    # Structured output check
    if "risk" in report_text:
        score += 1
    else:
        notes.append("Missing risk analysis")

    if "signal" in report_text:
        score += 1
    else:
        notes.append("Missing signals")

    if "asset" in report_text or "equities" in report_text:
        score += 1
    else:
        notes.append("No asset-level insight")

    if "scenario" in report_text:
        score += 1

    if "confidence" in report_text:
        score += 1

    return {
        "score": score,
        "max_score": 5,
        "notes": notes
    }
