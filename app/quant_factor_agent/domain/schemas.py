from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RunStatus(StrEnum):
    CREATED = "created"
    RESEARCHING = "researching"
    WAITING_APPROVAL = "waiting_approval"
    DEVELOPING = "developing"
    VALIDATING = "validating"
    EVALUATING = "evaluating"
    REVISING = "revising"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExperimentVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"


class Evidence(StrictModel):
    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    document_id: str
    title: str
    source_url: HttpUrl | None = None
    source_path: str | None = None
    page: int | None = Field(default=None, ge=1)
    chunk_id: str
    text: str = Field(min_length=20)
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    lexical_score: float = Field(default=0.0, ge=0.0)
    vector_score: float = Field(default=0.0, ge=0.0)
    rerank_score: float = Field(default=0.0, ge=0.0)


class FactorHypothesis(StrictModel):
    hypothesis_id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = Field(min_length=5, max_length=160)
    thesis: str = Field(min_length=20)
    economic_rationale: str = Field(min_length=20)
    formula_description: str = Field(min_length=10)
    required_fields: list[str] = Field(min_length=1)
    expected_direction: Literal["positive", "negative"]
    rebalance_frequency: Literal["monthly", "weekly", "daily"] = "monthly"
    lookback_periods: int = Field(ge=2, le=252)
    lag_periods: int = Field(default=1, ge=1, le=12)
    evidence_ids: list[str] = Field(min_length=1)
    leakage_risks: list[str] = Field(min_length=1)
    falsification_criteria: list[str] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("required_fields", "evidence_ids", mode="after")
    @classmethod
    def unique_non_empty(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty value is required")
        return list(dict.fromkeys(cleaned))


class FactorSpec(StrictModel):
    factor_id: str = Field(default_factory=lambda: uuid4().hex)
    hypothesis_id: str
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=10)
    function_name: str = Field(default="compute_factor", pattern=r"^[a-z_][a-z0-9_]*$")
    input_name: Literal["monthly_returns", "market_data"] = "monthly_returns"
    output_frequency: Literal["daily", "weekly", "monthly"] = "monthly"
    lookback_periods: int = Field(ge=2, le=120)
    lag_periods: int = Field(ge=1, le=12)
    higher_is_better: bool = True
    parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)


class FactorImplementation(StrictModel):
    implementation_id: str = Field(default_factory=lambda: uuid4().hex)
    spec: FactorSpec
    code: str = Field(min_length=40)
    source: Literal["mock_agent", "llm_agent", "human", "adapted"]
    upstream_reference: str | None = None
    code_sha256: str
    repair_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class FactorEvaluation(StrictModel):
    mean_rank_ic: float
    icir: float
    positive_ic_rate: float = Field(ge=0.0, le=1.0)
    top_bottom_spread_annualized: float
    annualized_return: float
    annualized_volatility: float = Field(ge=0.0)
    sharpe: float
    max_drawdown: float = Field(le=0.0)
    quantile_returns_annualized: list[float] = Field(min_length=2)
    quantile_monotonicity: float = Field(ge=-1.0, le=1.0)
    one_way_turnover: float = Field(ge=0.0)
    coverage: float = Field(ge=0.0, le=1.0)
    n_periods: int = Field(ge=1)
    passed_leakage_checks: bool
    forward_periods: int = Field(default=1, ge=1)
    evaluation_split: Literal["development", "validation", "walk_forward", "holdout"]


class ExperimentFeedback(StrictModel):
    verdict: ExperimentVerdict
    summary: str = Field(min_length=10)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def revision_requires_changes(self) -> ExperimentFeedback:
        if self.verdict == ExperimentVerdict.REVISE and not self.required_changes:
            raise ValueError("a revision verdict requires at least one requested change")
        return self


class RunRecord(StrictModel):
    run_id: str
    objective: str
    status: RunStatus
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=2, ge=0, le=10)
    approval: ApprovalDecision = ApprovalDecision.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
