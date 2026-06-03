from georisk_agent.agents.nodes_signals import build_tickers, CORE_TICKERS


def test_empty_isos_returns_only_core():
    tickers = build_tickers([])
    assert set(tickers) == set(CORE_TICKERS)


def test_china_adds_fxi_and_tsm():
    tickers = build_tickers(["CHN"])
    assert "FXI" in tickers
    assert "TSM" in tickers
    assert set(CORE_TICKERS).issubset(set(tickers))


def test_china_taiwan_deduplicates_fxi():
    tickers = build_tickers(["CHN", "TWN"])
    keys = list(tickers.keys())
    assert keys.count("FXI") == 1


def test_russia_saudi_deduplicates_natural_gas():
    tickers = build_tickers(["RUS", "SAU"])
    keys = list(tickers.keys())
    assert keys.count("NG=F") == 1
