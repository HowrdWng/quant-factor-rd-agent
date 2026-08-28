from __future__ import annotations

import numpy as np
import pandas as pd

from quant_factor_agent.domain.schemas import Evidence


def synthetic_monthly_returns(
    periods: int = 144,
    assets: int = 12,
    seed: int = 42,
) -> pd.DataFrame:
    """Create deterministic, weakly persistent returns for an offline demo."""
    rng = np.random.default_rng(seed)
    innovations = rng.normal(0.004, 0.035, size=(periods, assets))
    values = np.zeros_like(innovations)
    values[0] = innovations[0]
    for index in range(1, periods):
        values[index] = 0.45 * values[index - 1] + innovations[index]
    dates = pd.date_range("2012-01-31", periods=periods, freq="ME")
    columns = [f"ASSET_{index:02d}" for index in range(assets)]
    return pd.DataFrame(values, index=dates, columns=columns)


def demo_evidence() -> list[Evidence]:
    return [
        Evidence(
            document_id="internal-factor-guide",
            title="Cross-sectional momentum research notes",
            chunk_id="momentum-001",
            text=(
                "Medium-horizon cross-sectional momentum ranks assets by lagged cumulative returns. "
                "The signal must exclude the return of the portfolio formation period."
            ),
        ),
        Evidence(
            document_id="backtest-policy",
            title="Point-in-time backtest policy",
            chunk_id="pit-001",
            text=(
                "Every factor value at time t must use information available before t. The final holdout "
                "is sealed from research agents and may only be evaluated once after approval."
            ),
        ),
    ]
