from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


class WorkflowState(TypedDict, total=False):
    run_id: str
    objective: str
    auto_approve: bool
    status: str
    iteration: int
    max_iterations: int
    evidence: list[dict[str, Any]]
    data_context: list[dict[str, Any]]
    hypothesis: dict[str, Any]
    approval: str
    implementation: dict[str, Any]
    validation_errors: list[str]
    evaluation: dict[str, Any]
    evaluations: dict[str, dict[str, Any]]
    feedback: dict[str, Any]
    error: str
    event_log: Annotated[list[str], operator.add]
