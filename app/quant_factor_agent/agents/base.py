from __future__ import annotations

from typing import Protocol

from quant_factor_agent.domain.schemas import (
    Evidence,
    ExperimentFeedback,
    FactorHypothesis,
    FactorImplementation,
)


class ResearchAgent(Protocol):
    def propose(
        self,
        objective: str,
        evidence: list[Evidence],
        feedback: ExperimentFeedback | None = None,
        data_context: list[dict[str, object]] | None = None,
    ) -> FactorHypothesis: ...


class DevelopmentAgent(Protocol):
    def implement(
        self,
        hypothesis: FactorHypothesis,
        feedback: ExperimentFeedback | None = None,
        repair_count: int = 0,
    ) -> FactorImplementation: ...
