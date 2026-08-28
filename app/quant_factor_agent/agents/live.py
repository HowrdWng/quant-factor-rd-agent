from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, Field

from quant_factor_agent.domain.schemas import (
    Evidence,
    ExperimentFeedback,
    FactorHypothesis,
    FactorImplementation,
    FactorSpec,
)
from quant_factor_agent.factors.runtime import FactorCodePolicy
from quant_factor_agent.llm import ModelProvider


class ImplementationDraft(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=10)
    lookback_periods: int = Field(ge=2, le=120)
    lag_periods: int = Field(ge=1, le=12)
    input_name: str = Field(default="monthly_returns", pattern="^(monthly_returns|market_data)$")
    output_frequency: str = Field(default="monthly", pattern="^(daily|weekly|monthly)$")
    parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)
    code: str = Field(min_length=40)
    upstream_reference: str | None = None


def _feedback_text(feedback: ExperimentFeedback | None) -> str:
    if feedback is None:
        return "No prior feedback."
    return feedback.model_dump_json(indent=2)


class LLMResearchAgent:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def propose(
        self,
        objective: str,
        evidence: list[Evidence],
        feedback: ExperimentFeedback | None = None,
        data_context: list[dict[str, object]] | None = None,
    ) -> FactorHypothesis:
        evidence_json = [item.model_dump(mode="json") for item in evidence]
        schema_hint = FactorHypothesis.model_json_schema()
        return self.provider.generate_structured(
            system=(
                "You are a leakage-aware quantitative factor research agent. "
                "Return only valid JSON, no markdown. Propose one falsifiable factor hypothesis. "
                "Use only the provided evidence IDs. Use canonical required_fields from "
                "open/high/low/close/volume/amount/returns/monthly_returns and include only fields the "
                "factor formula actually uses. For a return-only monthly factor, use only monthly_returns; "
                "do not add close, volume, or returns. Define formula_description as one continuous raw "
                "score for each asset at each date. Ranking, top/bottom quantiles, portfolio aggregation, "
                "IC, and backtesting belong only in falsification criteria, never in the factor formula. "
                "Distinguish evidence-based claims from the new hypothesis. Do not claim live trading "
                "performance."
            ),
            user=(
                f"Objective:\n{objective}\n\n"
                f"Evidence:\n{evidence_json}\n\n"
                f"Structured data catalog matches (metadata only):\n{data_context or []}\n\n"
                f"Prior feedback:\n{_feedback_text(feedback)}\n\n"
                f"JSON schema to satisfy:\n{schema_hint}"
            ),
            schema=FactorHypothesis,
            temperature=0.2,
        )


class LLMDevelopmentAgent:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def implement(
        self,
        hypothesis: FactorHypothesis,
        feedback: ExperimentFeedback | None = None,
        repair_count: int = 0,
    ) -> FactorImplementation:
        allowed_methods = ", ".join(sorted(FactorCodePolicy().allowed_attributes))
        structured_fields = ("open", "high", "low", "close", "volume", "amount", "return")
        use_market_data = any(
            token in field.lower()
            for field in hypothesis.required_fields
            for token in structured_fields
            if field.lower() != "monthly_returns"
        )
        contract = (
            "compute_factor(market_data), where market_data is a dict of aligned pandas DataFrames "
            "with keys open/high/low/close/volume/amount/returns as requested. Every input used to form "
            "the signal must call .shift(k) with k >= 1 before rolling or cross-sectional operations. "
            "A valid structural example is: lagged = market_data['returns'].shift(1); "
            "factor = lagged.rolling(window=6, min_periods=6).mean(); return factor."
            if use_market_data
            else (
                "compute_factor(monthly_returns), where monthly_returns is a pandas DataFrame. "
                "The signal must use monthly_returns.shift(k) with k >= 1 before rolling or "
                "cross-sectional operations. A valid structural example is: "
                "lagged = monthly_returns.shift(1); "
                "factor = lagged.rolling(window=6, min_periods=6).mean(); return factor."
            )
        )
        draft = self.provider.generate_structured(
            system=(
                "You are a Python factor implementation agent. Return only valid JSON, no markdown. "
                f"Write exactly one function using this contract: {contract} "
                "The code may use pandas-style DataFrame methods and np, but must not import modules, "
                "read files, call network/process APIs, or use current/future returns. "
                "Return one continuous factor-score DataFrame with exactly the same index and columns as "
                "the input returns. Do not select stocks, calculate portfolio returns, reduce to a Series "
                "or scalar, or use quantile thresholds inside compute_factor. The deterministic evaluator, "
                "not this function, performs ranking, quantile selection, aggregation, and backtesting. "
                "Never use quantile, fillna, shape, multiply, boolean stock masks, or mean/sum with axis=1. "
                "Never use lambda, comprehensions, loops, imports, or helper functions. Even if the "
                "hypothesis mentions top and bottom portfolios, implement only the underlying per-asset "
                "continuous score. "
                f"The only allowed attribute methods are: {allowed_methods}. "
                "If prior validation feedback is present, fix every listed weakness explicitly."
            ),
            user=(
                f"Hypothesis:\n{hypothesis.model_dump_json(indent=2)}\n\n"
                f"Prior feedback:\n{_feedback_text(feedback)}\n\n"
                f"Return JSON matching this schema:\n{ImplementationDraft.model_json_schema()}"
            ),
            schema=ImplementationDraft,
            temperature=0.1,
        )
        spec = FactorSpec(
            hypothesis_id=hypothesis.hypothesis_id,
            name=draft.name,
            description=draft.description,
            input_name="market_data" if use_market_data else "monthly_returns",
            output_frequency=draft.output_frequency,
            lookback_periods=draft.lookback_periods,
            lag_periods=draft.lag_periods,
            parameters=draft.parameters,
        )
        digest = sha256(draft.code.encode("utf-8")).hexdigest()
        return FactorImplementation(
            spec=spec,
            code=draft.code,
            source="llm_agent",
            upstream_reference=draft.upstream_reference,
            code_sha256=digest,
            repair_count=repair_count,
        )
