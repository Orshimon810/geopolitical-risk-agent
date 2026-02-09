import streamlit as st

from georisk_agent.agents.graph import build_graph


st.set_page_config(
    page_title="Geopolitical Risk Agent",
    layout="wide",
)

st.title("🌍 Geopolitical Risk & Markets Agent")
st.markdown(
    "Analyze geopolitical risks and market impacts using an agentic RAG system."
)

# Input
query = st.text_area(
    "Enter a geopolitical or market-related question:",
    height=120,
    placeholder="e.g. What are the market implications of rising tensions between the US and China?",
)

run_button = st.button("Run Analysis")

if run_button and query.strip():
    with st.spinner("Running analysis..."):
        app = build_graph()
        result = app.invoke({"query": query})

    st.success("Analysis complete")

    # Planner
    if "plan" in result:
        st.subheader("🧠 Research Plan")
        for i, p in enumerate(result["plan"], 1):
            st.write(f"{i}. {p}")

    # Market Impacts
    if "market_impacts" in result:
        st.subheader("📊 Market Impacts")
        for m in result["market_impacts"]:
            st.markdown(f"- {m}")

    # Risks
    if "risks" in result:
        st.subheader("⚠️ Risks")
        for r in result["risks"]:
            st.markdown(f"- {r}")

    # Confidence
    if "confidence" in result:
        st.subheader("🎯 Confidence")
        st.write(result["confidence"])

    # Signals
    signals = result.get("signals", {})
    countries = signals.get("countries", {})

    if countries:
        st.subheader("🌐 External Signals (World Bank)")
        for iso, data in countries.items():
            st.write(f"**{iso}**: {data}")

else:
    st.info("Enter a question and click **Run Analysis**.")
