from __future__ import annotations

from hashlib import sha256

from quant_factor_agent.domain.schemas import (
    Evidence,
    ExperimentFeedback,
    FactorHypothesis,
    FactorImplementation,
    FactorSpec,
)


class MockResearchAgent:
    """Deterministic stand-in that exercises the exact production schemas."""

    def propose(
        self,
        objective: str,
        evidence: list[Evidence],
        feedback: ExperimentFeedback | None = None,
        data_context: list[dict[str, object]] | None = None,
    ) -> FactorHypothesis:
        lookback = 6 if feedback is None else 3
        return FactorHypothesis(
            title=f"Lagged cross-sectional momentum ({lookback} months)",
            thesis=(
                f"For the objective '{objective}', assets with stronger trailing returns "
                "may persist short term."
            ),
            economic_rationale=(
                "Investor underreaction and gradual information diffusion can produce "
                "medium-horizon return persistence."
            ),
            formula_description=f"Compound returns over {lookback} months using data lagged by one month.",
            required_fields=["monthly_returns"],
            expected_direction="positive",
            lookback_periods=lookback,
            lag_periods=1,
            evidence_ids=[item.evidence_id for item in evidence],
            leakage_risks=[
                "Using current-month returns in the signal",
                "Selecting parameters on the final holdout",
            ],
            falsification_criteria=[
                "Development mean Rank IC is non-positive",
                "Cost-adjusted top-bottom spread is non-positive",
            ],
        )


class MockDevelopmentAgent:
    def implement(
        self,
        hypothesis: FactorHypothesis,
        feedback: ExperimentFeedback | None = None,
        repair_count: int = 0,
    ) -> FactorImplementation:
        window = hypothesis.lookback_periods
        code = (
            "def compute_factor(monthly_returns):\n"
            "    shifted = monthly_returns.shift(1)\n"
            f"    factor = (1.0 + shifted).rolling({window}, min_periods={window})"
            ".apply(np.prod, raw=True) - 1.0\n"
            "    return factor\n"
        )
        spec = FactorSpec(
            hypothesis_id=hypothesis.hypothesis_id,
            name=f"momentum_{window}m_lag1",
            description=f"Lagged {window}-month compounded return factor.",
            lookback_periods=window,
            lag_periods=hypothesis.lag_periods,
            parameters={"window": window},
        )
        digest = sha256(code.encode("utf-8")).hexdigest()
        return FactorImplementation(
            spec=spec,
            code=code,
            source="mock_agent",
            code_sha256=digest,
            repair_count=repair_count,
        )
