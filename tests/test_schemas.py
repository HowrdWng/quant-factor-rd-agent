import pytest
from pydantic import ValidationError
from quant_factor_agent.domain.schemas import FactorHypothesis


def valid_hypothesis_payload() -> dict:
    return {
        "title": "Lagged six month momentum",
        "thesis": "Assets with stronger lagged returns may continue outperforming over the next month.",
        "economic_rationale": (
            "Investor underreaction can create gradual price adjustment after information arrives."
        ),
        "formula_description": "Compound six lagged monthly returns and rank across assets.",
        "required_fields": ["monthly_returns"],
        "expected_direction": "positive",
        "lookback_periods": 6,
        "lag_periods": 1,
        "evidence_ids": ["evidence-1"],
        "leakage_risks": ["Current-month return accidentally included"],
        "falsification_criteria": ["Mean development Rank IC is not positive"],
    }


def test_hypothesis_requires_information_lag() -> None:
    payload = valid_hypothesis_payload()
    payload["lag_periods"] = 0
    with pytest.raises(ValidationError):
        FactorHypothesis.model_validate(payload)


def test_hypothesis_rejects_unknown_fields() -> None:
    payload = valid_hypothesis_payload()
    payload["secret_future_return"] = 1.0
    with pytest.raises(ValidationError):
        FactorHypothesis.model_validate(payload)
