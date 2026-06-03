import datetime
import streamlit as st

from georisk_agent.agents.graph import build_graph
from georisk_agent.app.config import settings


# -------------------------------------------------------------------
# Page config
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Geopolitical Risk & Markets Agent",
    layout="wide",
)

# -------------------------------------------------------------------
# Rate-limit state
# -------------------------------------------------------------------
SESSION_LIMIT = settings.session_query_limit
DAILY_LIMIT = settings.daily_query_limit


@st.cache_resource
def get_budget() -> dict:
    """Shared counter across all sessions within this server process."""
    return {"date": None, "count": 0}


def _reset_budget_if_new_day(budget: dict) -> None:
    today = datetime.date.today().isoformat()
    if budget["date"] != today:
        budget["date"] = today
        budget["count"] = 0


budget = get_budget()
_reset_budget_if_new_day(budget)

if "session_queries" not in st.session_state:
    st.session_state["session_queries"] = 0

# -------------------------------------------------------------------
# Page header
# -------------------------------------------------------------------
st.title("🌍 Geopolitical Risk & Markets Agent")
st.markdown(
    "Analyze geopolitical risks and market impacts using an **agentic RAG system** "
    "with external macroeconomic signals."
)

session_used = st.session_state["session_queries"]
st.caption(
    f"Session: {session_used}/{SESSION_LIMIT} queries used  |  "
    f"Daily: {budget['count']}/{DAILY_LIMIT} queries used"
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
    if st.session_state["session_queries"] >= SESSION_LIMIT:
        st.warning(
            f"You have reached the session limit of {SESSION_LIMIT} queries. "
            "Refresh the page to start a new session."
        )
        st.stop()

    if budget["count"] >= DAILY_LIMIT:
        st.warning(
            f"The daily limit of {DAILY_LIMIT} queries has been reached. "
            "Please come back tomorrow."
        )
        st.stop()

    with st.spinner("Running analysis..."):
        app = build_graph()
        result = app.invoke({"query": query})

    st.session_state["session_queries"] += 1
    budget["count"] += 1

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

    # ---------------------------------------------------------------
    # Live Market Prices
    # ---------------------------------------------------------------
    market_data = signals.get("market_data", {})
    ok_entries = {s: d for s, d in market_data.items() if d.get("status") == "ok"}
    if ok_entries:
        st.subheader("📈 Live Market Prices")
        cols = st.columns(min(len(ok_entries), 4))
        for i, (symbol, d) in enumerate(ok_entries.items()):
            chg = d.get("change_1d_pct")
            cols[i % 4].metric(
                label=d["label"],
                value=str(d["price"]),
                delta=f"{chg:+.2f}%" if chg is not None else None,
            )

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
