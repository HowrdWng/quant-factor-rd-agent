import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
from quant_factor_agent.data import DataCatalogTool, LocalCsmarCsvProvider, MarketDataRequest
from quant_factor_agent.domain.schemas import FactorImplementation, FactorSpec
from quant_factor_agent.evaluation import FactorEvaluator
from quant_factor_agent.execution import OrderRequest, PaperBrokerAdapter
from quant_factor_agent.factors import FactorRuntime


def test_local_csmar_provider_normalizes_ohlcv(tmp_path: Path) -> None:
    export = tmp_path / "csmar_exports"
    export.mkdir()
    rows = []
    for date in pd.date_range("2024-01-01", periods=4):
        for asset, price in (("1", 10.0), ("2", 20.0)):
            rows.append(
                {"TradingDate": date.date(), "Symbol": asset, "OpenPrice": price,
                 "HighPrice": price + 1, "LowPrice": price - 1, "ClosePrice": price + 0.5,
                 "Volume": 1_000, "ChangeRatio": 0.01}
            )
    pd.DataFrame(rows).to_csv(export / "trd_fwardquotation.csv", index=False)

    bundle = LocalCsmarCsvProvider(tmp_path).load(MarketDataRequest(frequency="daily"))

    assert bundle.provider == "local_csmar_csv"
    assert set(bundle.frames) >= {"open", "high", "low", "close", "volume", "returns"}
    assert list(bundle.returns.columns) == ["000001", "000002"]


def test_market_data_factor_contract() -> None:
    dates = pd.date_range("2020-01-01", periods=8, freq="D")
    columns = ["A", "B", "C", "D"]
    returns = pd.DataFrame(np.arange(32).reshape(8, 4) / 10_000, index=dates, columns=columns)
    volume = pd.DataFrame(np.arange(32).reshape(8, 4) + 100, index=dates, columns=columns)
    code = (
        "def compute_factor(market_data):\n"
        "    volume = market_data['volume'].shift(1)\n"
        "    factor = volume / volume.rolling(3, min_periods=3).mean()\n"
        "    return factor\n"
    )
    spec = FactorSpec(
        hypothesis_id="hypothesis", name="volume_surprise", description="Lagged volume surprise",
        input_name="market_data", output_frequency="daily", lookback_periods=3, lag_periods=1,
    )
    implementation = FactorImplementation(
        spec=spec, code=code, source="human", code_sha256=sha256(code.encode()).hexdigest()
    )

    output = FactorRuntime().execute(implementation, {"returns": returns, "volume": volume})

    assert output.shape == returns.shape
    assert output.iloc[-1].notna().all()


def test_paper_broker_applies_rules_and_never_live(tmp_path: Path) -> None:
    broker = PaperBrokerAdapter(tmp_path / "paper.db")
    broker.update_market_prices({"AAPL": 100.0})
    order = broker.place_order(OrderRequest(symbol="AAPL", side="buy", quantity=10))

    assert order["status"] == "filled"
    assert broker.get_positions()[0]["quantity"] == 10
    assert broker.get_account()["live_trading"] is False


def test_evaluator_reports_forward_horizon_and_quantiles() -> None:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2020-01-01", periods=80, freq="D")
    columns = [f"A{index}" for index in range(10)]
    returns = pd.DataFrame(rng.normal(0, 0.01, (80, 10)), index=dates, columns=columns)
    factor = returns.shift(1).rolling(5).mean()

    evaluation = FactorEvaluator(periods_per_year=252, forward_periods=5).evaluate(factor, returns)

    assert evaluation.forward_periods == 5
    assert len(evaluation.quantile_returns_annualized) == 5
    assert evaluation.max_drawdown <= 0


def test_data_catalog_is_metadata_tool_not_rag(tmp_path: Path) -> None:
    payload = [
        {
            "table_name": "trd_dalyr",
            "table_code": "TRD_Dalyr",
            "chinese_name": "日个股回报率文件",
            "data_rows": 100,
            "file_size_mb": 1.0,
            "columns": [{"name": "Dnshrtrd", "label": "日个股交易股数"}],
        }
    ]
    (tmp_path / "csmar_tables.json").write_text(json.dumps(payload), encoding="utf-8")

    result = DataCatalogTool(tmp_path).search("研究成交量因子")

    assert result[0]["table"] == "trd_dalyr"
    assert result[0]["export_ready"] is False
