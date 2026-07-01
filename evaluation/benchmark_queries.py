BENCHMARK_QUERIES = [

    # -----------------------------
    # Core capability tests
    # -----------------------------

    {
        "query": "What geopolitical scenario is most likely to trigger a global risk-off event within the next 12 months?",
        "focus": "scenario_generation"
    },
    {
        "query": "How could a disruption in the Strait of Hormuz impact global inflation and equity markets?",
        "focus": "transmission_mechanisms"
    },
    {
        "query": "Identify early warning signals that may indicate an emerging energy supply shock.",
        "focus": "signals"
    },
    {
        "query": "Which asset classes typically reprice first during geopolitical escalations?",
        "focus": "market_behavior"
    },
    {
        "query": "Construct a plausible but underpriced geopolitical risk and explain why markets may be misaligned.",
        "focus": "second_order_reasoning"
    },

    # -----------------------------
    # 🔥 Senior-level stress tests
    # -----------------------------

    {
        "query": "Is the market currently underpricing any geopolitical risk?",
        "focus": "ambiguity_handling"
    },

    {
        "query": "Could a political shift in a mid-sized emerging economy trigger contagion across global credit markets?",
        "focus": "thin_evidence_calibration"
    },

    {
        "query": "Why is a military conflict in Antarctica likely within the next decade?",
        "focus": "false_premise_detection"
    },

    # -----------------------------
    # P0/P1 fix regression tests
    # -----------------------------

    # Test 9: speculative event — Fix 2 (hedge block injection)
    # Base case must NOT assume the event has already happened.
    {
        "query": (
            "Unconfirmed reports suggest Iran may be planning to blockade the Strait of Hormuz. "
            "If these reports are accurate, how would this affect energy markets and oil-exposed equities?"
        ),
        "focus": "event_certainty_speculative",
    },

    # Test 10: vague actor — Fix 2 (vague actor hedge block)
    # Scenarios must acknowledge missing actor/geography and avoid precise numeric forecasts.
    {
        "query": (
            "A large unnamed commodity-producing country has just announced sweeping export "
            "restrictions on critical minerals. What are the global market implications?"
        ),
        "focus": "event_certainty_vague_actor",
    },

    # Test 11: alleged positive event with semiconductor portfolio — Fixes 1, 2, 3
    # Alleged certainty must produce conditional framing; portfolio_net must reflect
    # post-validation verdicts; validator must see full 300-char prose.
    {
        "query": (
            "Reports suggest the US and China may be nearing a deal to ease semiconductor "
            "export restrictions. How would this affect semiconductor-heavy portfolios?"
        ),
        "focus": "event_certainty_alleged_positive",
        "portfolio": [
            {"ticker": "NVDA", "name": "NVIDIA Corporation",                   "asset_type": "stock"},
            {"ticker": "ASML", "name": "ASML Holding NV",                       "asset_type": "stock"},
            {"ticker": "TSM",  "name": "Taiwan Semiconductor Manufacturing",    "asset_type": "stock"},
            {"ticker": "AAPL", "name": "Apple Inc.",                            "asset_type": "stock"},
        ],
    },

    # Test 12: confirmed Taiwan disruption with semiconductor portfolio — Fixes 1, 3
    # Confirmed event (no hedge block); tests portfolio_net correctness and full-prose validation.
    {
        "query": (
            "China has announced military exercises near Taiwan that are disrupting TSMC's "
            "logistics and supply chains. What is the impact on semiconductor stocks?"
        ),
        "focus": "transmission_mechanisms_semiconductor",
        "portfolio": [
            {"ticker": "NVDA", "name": "NVIDIA Corporation",                   "asset_type": "stock"},
            {"ticker": "ASML", "name": "ASML Holding NV",                       "asset_type": "stock"},
            {"ticker": "TSM",  "name": "Taiwan Semiconductor Manufacturing",    "asset_type": "stock"},
            {"ticker": "AAPL", "name": "Apple Inc.",                            "asset_type": "stock"},
            {"ticker": "LMT",  "name": "Lockheed Martin Corporation",           "asset_type": "stock"},
        ],
    },


    # Test 13: confirmed trade policy — EU Chinese EV tariffs
    # Confirmed regulatory event; tests transmission mechanism reasoning across
    # European OEMs, Chinese exporters, and battery supply chains.
    {
        "query": (
            "The EU has confirmed provisional tariffs of up to 38% on Chinese electric vehicle imports. "
            "How will this affect European automakers, Chinese EV exporters, and battery supply chains?"
        ),
        "focus": "transmission_mechanisms",
    },

]
