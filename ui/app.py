import streamlit as st

from georisk_agent.agents.graph import build_graph


# -------------------------------------------------------------------
# Page config
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Geopolitical Risk & Markets Agent",
    layout="wide",
)

st.title("🌍 Geopolitical Risk & Markets Agent")
st.markdown(
    "Analyze geopolitical risks and market impacts using an **agentic RAG system** "
    "with external macroeconomic signals."
)

# -------------------------------------------------------------------
# User input
# -------------------------------------------------------------------
query = st.text_area(
    "Enter a geopolitical or market-related question:",
    height=120,
    placeholder=(
        "e.g. What second- and third-order economic effects could result from "
        "a prolonged conflict between Israel and Iran?"
    ),
)

run_button = st.button("Run Analysis")

# -------------------------------------------------------------------
# Run analysis
# -------------------------------------------------------------------
if run_button and query.strip():
    with st.spinner("Running analysis..."):
        app = build_graph()
        result = app.invoke({"query": query})

    st.success("Analysis complete")

    # ---------------------------------------------------------------
    # Research Plan
    # ---------------------------------------------------------------
    plan = result.get("plan", [])
    if plan:
        with st.expander("🧠 Research Plan", expanded=True):
            for i, p in enumerate(plan, 1):
                st.write(f"{i}. {p}")

    # ---------------------------------------------------------------
    # Market Impacts
    # ---------------------------------------------------------------
    market_impacts = result.get("market_impacts", [])
    if market_impacts:
        with st.expander("📊 Market Impacts", expanded=True):
            for m in market_impacts:
                st.markdown(f"- {m}")

    # ---------------------------------------------------------------
    # Risks
    # ---------------------------------------------------------------
    risks = result.get("risks", [])
    if risks:
        with st.expander("⚠️ Risks", expanded=False):
            for r in risks:
                st.markdown(f"- {r}")

    # ---------------------------------------------------------------
    # Scenarios
    # ---------------------------------------------------------------
    scenarios = result.get("scenarios", [])
    if scenarios:
        with st.expander("🔮 Scenarios", expanded=True):
            for s in scenarios:
                st.markdown(f"- {s}")

    # ---------------------------------------------------------------
    # Investor Takeaway
    # ---------------------------------------------------------------
    investor_takeaway = result.get("investor_takeaway", [])
    if investor_takeaway:
        with st.expander("💡 Investor Takeaway", expanded=True):
            for t in investor_takeaway:
                st.markdown(f"- {t}")

    # ---------------------------------------------------------------
    # Confidence
    # ---------------------------------------------------------------
    confidence = result.get("confidence")
    if confidence:
        color = {"Low": "🔴", "Medium": "🟡", "High": "🟢"}.get(confidence, "")
        st.subheader("🎯 Confidence")
        st.markdown(f"**{color} {confidence}**")

    # ---------------------------------------------------------------
    # External Signals
    # ---------------------------------------------------------------
    signals = result.get("signals", {})
    countries = signals.get("countries", {})

    if countries:
        st.subheader("🌐 External Signals (World Bank)")
        for iso, data in countries.items():
            parts = []
            trade = data.get("trade_gdp", {})
            if trade.get("status") == "ok":
                parts.append(f"Trade: **{trade['value']:.1f}% of GDP** ({trade['year']})")
            oil = data.get("oil_rents", {})
            if oil.get("status") == "ok":
                parts.append(f"Oil Rents: **{oil['value']:.1f}% of GDP** ({oil['year']})")
            if parts:
                st.markdown(f"**{iso}** — {', '.join(parts)}")
            else:
                st.markdown(f"**{iso}** — No recent data available")

else:
    st.info("Enter a question and click **Run Analysis**.")

# -------------------------------------------------------------------
# Disclaimer
# -------------------------------------------------------------------
st.divider()
st.caption(
    "This tool provides analytical insights for educational and research purposes only "
    "and does not constitute investment advice."
)
