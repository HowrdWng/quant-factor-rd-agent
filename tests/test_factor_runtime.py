from hashlib import sha256

import pandas as pd
import pytest
from quant_factor_agent.domain.schemas import FactorImplementation, FactorSpec
from quant_factor_agent.factors import FactorRuntime, FactorValidationError


def implementation(code: str) -> FactorImplementation:
    spec = FactorSpec(
        hypothesis_id="hyp-1",
        name="momentum_3m_lag1",
        description="Three-month lagged compounded return factor.",
        lookback_periods=3,
        lag_periods=1,
    )
    return FactorImplementation(
        spec=spec,
        code=code,
        source="human",
        code_sha256=sha256(code.encode()).hexdigest(),
    )


def test_runtime_executes_valid_lagged_factor() -> None:
    code = (
        "def compute_factor(monthly_returns):\n"
        "    shifted = monthly_returns.shift(1)\n"
        "    factor = shifted.rolling(3, min_periods=3).mean()\n"
        "    return factor\n"
    )
    returns = pd.DataFrame({"A": [0.1, 0.2, 0.3, 0.4], "B": [0.0, 0.1, 0.0, 0.1]})
    output = FactorRuntime().execute(implementation(code), returns)
    assert output.loc[3, "A"] == pytest.approx(0.2)


@pytest.mark.parametrize(
    "code, expected",
    [
        (
            "import os\ndef compute_factor(monthly_returns):\n return monthly_returns.shift(1)\n",
            "exactly one",
        ),
        ("def compute_factor(monthly_returns):\n return monthly_returns.mean()\n", "must explicitly lag"),
        (
            "def compute_factor(monthly_returns):\n return monthly_returns.shift(1).__class__\n",
            "attribute is not allowed",
        ),
        (
            "def compute_factor(monthly_returns):\n return monthly_returns.shift(0)\n",
            "shift periods must be",
        ),
    ],
)
def test_runtime_rejects_unsafe_or_noncausal_code(code: str, expected: str) -> None:
    with pytest.raises(FactorValidationError, match=expected):
        FactorRuntime().policy.validate(code)
