from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from quant_factor_agent.agents.base import DevelopmentAgent, ResearchAgent
from quant_factor_agent.data import DataCatalogTool
from quant_factor_agent.domain.schemas import (
    ApprovalDecision,
    Evidence,
    ExperimentFeedback,
    ExperimentVerdict,
    FactorEvaluation,
    FactorHypothesis,
    FactorImplementation,
    RunRecord,
    RunStatus,
)
from quant_factor_agent.evaluation import FactorEvaluator, TemporalDataVault, TemporalSplit
from quant_factor_agent.factors import FactorRuntime, FactorValidationError
from quant_factor_agent.llm import ModelProviderError
from quant_factor_agent.retrieval import Retriever
from quant_factor_agent.storage import ExperimentStore
from quant_factor_agent.workflow.state import WorkflowState


@dataclass(frozen=True)
class WorkflowDependencies:
    research_agent: ResearchAgent
    development_agent: DevelopmentAgent
    retriever: Retriever
    runtime: FactorRuntime
    evaluator: FactorEvaluator
    store: ExperimentStore
    monthly_returns: pd.DataFrame
    artifact_dir: Path
    market_data: dict[str, pd.DataFrame] | None = None
    data_catalog: DataCatalogTool | None = None
    checkpoint_path: Path | None = None
    mode: str = "mock"


class ResearchDevelopmentWorkflow:
    """Bounded factor R&D graph adapted from proven RD-Agent/TradingAgents patterns."""

    def __init__(self, dependencies: WorkflowDependencies) -> None:
        self.dependencies = dependencies
        self.data_vault = TemporalDataVault(dependencies.monthly_returns)
        self._checkpoint_connection: sqlite3.Connection | None = None
        if dependencies.checkpoint_path is None:
            self.checkpointer = InMemorySaver()
        else:
            dependencies.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._checkpoint_connection = sqlite3.connect(
                dependencies.checkpoint_path,
                check_same_thread=False,
            )
            self.checkpointer = SqliteSaver(self._checkpoint_connection)
            self.checkpointer.setup()
        self.workflow = self._build_graph()
        self.graph = self.workflow.compile(checkpointer=self.checkpointer)

    def close(self) -> None:
        if self._checkpoint_connection is not None:
            self._checkpoint_connection.close()
            self._checkpoint_connection = None

    def __enter__(self) -> ResearchDevelopmentWorkflow:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _record(self, state: WorkflowState, event_type: str, payload: object) -> None:
        self.dependencies.store.log_event(state["run_id"], event_type, payload)

    def retrieve_node(self, state: WorkflowState) -> dict:
        evidence = self.dependencies.retriever.search(state["objective"], limit=5)
        data_context = (
            self.dependencies.data_catalog.search(state["objective"], limit=8)
            if self.dependencies.data_catalog is not None
            else []
        )
        payload = [item.model_dump(mode="json") for item in evidence]
        self._record(state, "evidence_retrieved", payload)
        self._record(state, "data_catalog_searched", data_context)
        self.dependencies.store.update_run(state["run_id"], status=RunStatus.RESEARCHING)
        return {
            "evidence": payload,
            "data_context": data_context,
            "status": RunStatus.RESEARCHING.value,
            "event_log": [f"retrieved:{len(payload)}"],
        }

    def research_node(self, state: WorkflowState) -> dict:
        evidence = [Evidence.model_validate(item) for item in state["evidence"]]
        feedback_payload = state.get("feedback")
        feedback = ExperimentFeedback.model_validate(feedback_payload) if feedback_payload else None
        try:
            hypothesis = self.dependencies.research_agent.propose(
                state["objective"], evidence, feedback, state.get("data_context", [])
            )
        except ModelProviderError as exc:
            self.dependencies.store.update_run(state["run_id"], status=RunStatus.FAILED)
            self._record(state, "model_provider_error", {"node": "research", "error": str(exc)})
            raise
        self._record(state, "hypothesis_proposed", hypothesis)
        self.dependencies.store.update_run(
            state["run_id"],
            status=RunStatus.WAITING_APPROVAL,
            approval=ApprovalDecision.PENDING.value,
        )
        return {
            "hypothesis": hypothesis.model_dump(mode="json"),
            "approval": ApprovalDecision.PENDING.value,
            "status": RunStatus.WAITING_APPROVAL.value,
            "event_log": [f"hypothesis:{hypothesis.hypothesis_id}"],
        }

    def approval_node(self, state: WorkflowState) -> dict:
        if state.get("auto_approve", False):
            decision = ApprovalDecision.APPROVED
            mode = "automatic"
        else:
            response = interrupt(
                {
                    "type": "factor_hypothesis_approval",
                    "run_id": state["run_id"],
                    "hypothesis": state["hypothesis"],
                    "allowed_decisions": [
                        ApprovalDecision.APPROVED.value,
                        ApprovalDecision.REJECTED.value,
                    ],
                }
            )
            decision = ApprovalDecision(str(response))
            mode = "human"
        status = RunStatus.DEVELOPING if decision == ApprovalDecision.APPROVED else RunStatus.REJECTED
        self.dependencies.store.update_run(state["run_id"], status=status, approval=decision.value)
        self._record(state, "approval_decision", {"decision": decision.value, "mode": mode})
        return {
            "approval": decision.value,
            "status": status.value,
            "event_log": [f"approval:{decision.value}"],
        }

    @staticmethod
    def route_after_approval(state: WorkflowState) -> str:
        return "develop" if state["approval"] == ApprovalDecision.APPROVED.value else "finalize"

    def develop_node(self, state: WorkflowState) -> dict:
        hypothesis = FactorHypothesis.model_validate(state["hypothesis"])
        feedback_payload = state.get("feedback")
        feedback = ExperimentFeedback.model_validate(feedback_payload) if feedback_payload else None
        try:
            implementation = self.dependencies.development_agent.implement(
                hypothesis,
                feedback,
                repair_count=state.get("iteration", 0),
            )
        except ModelProviderError as exc:
            self.dependencies.store.update_run(state["run_id"], status=RunStatus.FAILED)
            self._record(state, "model_provider_error", {"node": "develop", "error": str(exc)})
            raise
        self._record(state, "implementation_generated", implementation)
        self.dependencies.store.update_run(state["run_id"], status=RunStatus.VALIDATING)
        return {
            "implementation": implementation.model_dump(mode="json"),
            "validation_errors": [],
            "status": RunStatus.VALIDATING.value,
            "event_log": [f"implementation:{implementation.code_sha256[:12]}"],
        }

    def validate_node(self, state: WorkflowState) -> dict:
        implementation = FactorImplementation.model_validate(state["implementation"])
        errors: list[str] = []
        try:
            self.dependencies.runtime.policy.validate(implementation.code)
            factor_input: pd.DataFrame | dict[str, pd.DataFrame]
            if implementation.spec.input_name == "market_data":
                if self.dependencies.market_data is None:
                    raise FactorValidationError("factor requires market_data but no provider supplied it")
                factor_input = {name: frame.head(24) for name, frame in self.dependencies.market_data.items()}
            else:
                factor_input = self.dependencies.monthly_returns.head(24)
            factor = self.dependencies.runtime.execute(implementation, factor_input)
            if factor.notna().sum().sum() == 0:
                errors.append("factor smoke test returned only missing values")
        except (FactorValidationError, ValueError, TypeError) as exc:
            errors.append(str(exc))

        if errors and state.get("iteration", 0) >= state.get("max_iterations", 2):
            status = RunStatus.FAILED
        elif errors:
            status = RunStatus.REVISING
        else:
            status = RunStatus.EVALUATING
        self.dependencies.store.update_run(
            state["run_id"], status=status, iteration=state.get("iteration", 0)
        )
        self._record(state, "implementation_validated", {"errors": errors})
        return {
            "validation_errors": errors,
            "status": status.value,
            "event_log": ["validation:failed" if errors else "validation:passed"],
        }

    @staticmethod
    def route_after_validation(state: WorkflowState) -> str:
        if not state.get("validation_errors"):
            return "evaluate"
        if state.get("iteration", 0) < state.get("max_iterations", 2):
            return "repair"
        return "finalize"

    def repair_node(self, state: WorkflowState) -> dict:
        iteration = state.get("iteration", 0) + 1
        feedback = ExperimentFeedback(
            verdict=ExperimentVerdict.REVISE,
            summary="Generated factor implementation failed deterministic validation.",
            weaknesses=state.get("validation_errors", []),
            required_changes=["Repair every validation error without weakening the safety policy."],
        )
        self.dependencies.store.update_run(state["run_id"], status=RunStatus.REVISING, iteration=iteration)
        self._record(state, "repair_requested", feedback)
        return {
            "iteration": iteration,
            "feedback": feedback.model_dump(mode="json"),
            "status": RunStatus.REVISING.value,
            "event_log": [f"repair:{iteration}"],
        }

    def evaluate_node(self, state: WorkflowState) -> dict:
        implementation = FactorImplementation.model_validate(state["implementation"])
        research_returns = self.data_vault.research_frame()
        if implementation.spec.input_name == "market_data":
            if self.dependencies.market_data is None:
                raise FactorValidationError("factor requires market_data but no provider supplied it")
            research_input = {
                name: frame.loc[research_returns.index, research_returns.columns]
                for name, frame in self.dependencies.market_data.items()
            }
            full_factor = self.dependencies.runtime.execute(implementation, research_input)
        else:
            full_factor = self.dependencies.runtime.execute(implementation, research_returns)
        evaluations: dict[str, FactorEvaluation] = {}
        for split in (
            TemporalSplit.DEVELOPMENT,
            TemporalSplit.VALIDATION,
            TemporalSplit.WALK_FORWARD,
        ):
            split_returns = self.data_vault.frame(split)
            evaluations[split.value] = self.dependencies.evaluator.evaluate(
                full_factor.loc[split_returns.index],
                split_returns,
                split=split.value,
                passed_leakage_checks=True,
            )
        evaluation = evaluations[TemporalSplit.VALIDATION.value]
        metrics = {
            f"{split_name}.{metric_name}": metric_value
            for split_name, split_evaluation in evaluations.items()
            for metric_name, metric_value in {
                "mean_rank_ic": split_evaluation.mean_rank_ic,
                "icir": split_evaluation.icir,
                "positive_ic_rate": split_evaluation.positive_ic_rate,
                "spread_annualized": split_evaluation.top_bottom_spread_annualized,
                "annualized_return": split_evaluation.annualized_return,
                "annualized_volatility": split_evaluation.annualized_volatility,
                "sharpe": split_evaluation.sharpe,
                "max_drawdown": split_evaluation.max_drawdown,
                "quantile_monotonicity": split_evaluation.quantile_monotonicity,
                "one_way_turnover": split_evaluation.one_way_turnover,
                "coverage": split_evaluation.coverage,
            }.items()
        }
        self.dependencies.store.log_metrics(state["run_id"], metrics, step=state.get("iteration", 0))
        serialized = {name: item.model_dump(mode="json") for name, item in evaluations.items()}
        self._record(state, "factor_evaluated", serialized)
        return {
            "evaluation": evaluation.model_dump(mode="json"),
            "evaluations": serialized,
            "event_log": [f"validation:ic={evaluation.mean_rank_ic:.4f}"],
        }

    def feedback_node(self, state: WorkflowState) -> dict:
        evaluations = {
            name: FactorEvaluation.model_validate(payload) for name, payload in state["evaluations"].items()
        }
        development = evaluations[TemporalSplit.DEVELOPMENT.value]
        evaluation = evaluations[TemporalSplit.VALIDATION.value]
        walk_forward = evaluations[TemporalSplit.WALK_FORWARD.value]
        passed = (
            all(item.passed_leakage_checks for item in evaluations.values())
            and development.mean_rank_ic > 0.02
            and evaluation.coverage >= 0.60
            and evaluation.mean_rank_ic > 0
            and evaluation.top_bottom_spread_annualized > 0
            and walk_forward.mean_rank_ic > 0
        )
        can_revise = state.get("iteration", 0) < state.get("max_iterations", 2)
        if passed:
            verdict = ExperimentVerdict.ACCEPT
            status = RunStatus.ACCEPTED
            changes: list[str] = []
        elif can_revise:
            verdict = ExperimentVerdict.REVISE
            status = RunStatus.REVISING
            changes = ["Revise the factor horizon while keeping a one-period information lag."]
        else:
            verdict = ExperimentVerdict.REJECT
            status = RunStatus.REJECTED
            changes = []

        feedback = ExperimentFeedback(
            verdict=verdict,
            summary=(
                f"Validation produced mean Rank IC {evaluation.mean_rank_ic:.4f}; "
                f"walk-forward Rank IC was {walk_forward.mean_rank_ic:.4f}."
            ),
            strengths=(
                ["Passed deterministic look-ahead policy across all research splits"]
                if all(item.passed_leakage_checks for item in evaluations.values())
                else []
            ),
            weaknesses=[] if passed else ["Factor did not clear all development acceptance gates"],
            required_changes=changes,
        )
        next_iteration = state.get("iteration", 0) + (1 if verdict == ExperimentVerdict.REVISE else 0)
        self.dependencies.store.update_run(state["run_id"], status=status, iteration=next_iteration)
        self._record(state, "feedback_generated", feedback)
        return {
            "feedback": feedback.model_dump(mode="json"),
            "status": status.value,
            "iteration": next_iteration,
            "event_log": [f"verdict:{verdict.value}"],
        }

    @staticmethod
    def route_after_feedback(state: WorkflowState) -> str:
        feedback = ExperimentFeedback.model_validate(state["feedback"])
        return "research" if feedback.verdict == ExperimentVerdict.REVISE else "finalize"

    def finalize_node(self, state: WorkflowState) -> dict:
        status = RunStatus(state.get("status", RunStatus.FAILED.value))
        self.dependencies.store.update_run(
            state["run_id"],
            status=status,
            iteration=state.get("iteration", 0),
            approval=state.get("approval", ApprovalDecision.PENDING.value),
        )
        self._record(state, "run_finalized", {"status": status.value})
        return {"event_log": [f"final:{status.value}"]}

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(WorkflowState)
        workflow.add_node("retrieve", self.retrieve_node)
        workflow.add_node("research", self.research_node)
        workflow.add_node("approval", self.approval_node)
        workflow.add_node("develop", self.develop_node)
        workflow.add_node("validate", self.validate_node)
        workflow.add_node("repair", self.repair_node)
        workflow.add_node("evaluate", self.evaluate_node)
        workflow.add_node("feedback", self.feedback_node)
        workflow.add_node("finalize", self.finalize_node)

        workflow.add_edge(START, "retrieve")
        workflow.add_edge("retrieve", "research")
        workflow.add_edge("research", "approval")
        workflow.add_conditional_edges(
            "approval",
            self.route_after_approval,
            {"develop": "develop", "finalize": "finalize"},
        )
        workflow.add_edge("develop", "validate")
        workflow.add_conditional_edges(
            "validate",
            self.route_after_validation,
            {"evaluate": "evaluate", "repair": "repair", "finalize": "finalize"},
        )
        workflow.add_edge("repair", "develop")
        workflow.add_edge("evaluate", "feedback")
        workflow.add_conditional_edges(
            "feedback",
            self.route_after_feedback,
            {"research": "research", "finalize": "finalize"},
        )
        workflow.add_edge("finalize", END)
        return workflow

    def run(
        self,
        objective: str,
        *,
        auto_approve: bool = False,
        max_iterations: int = 2,
        run_id: str | None = None,
    ) -> WorkflowState:
        selected_run_id = run_id or uuid4().hex
        record = RunRecord(
            run_id=selected_run_id,
            objective=objective,
            status=RunStatus.CREATED,
            max_iterations=max_iterations,
            metadata={
                "workflow_version": "0.1.0",
                "agent_mode": self.dependencies.mode,
                "offline": self.dependencies.mode != "live",
            },
        )
        self.dependencies.store.create_run(record)
        self.dependencies.store.log_params(
            selected_run_id,
            {"objective": objective, "auto_approve": auto_approve, "max_iterations": max_iterations},
        )
        initial: WorkflowState = {
            "run_id": selected_run_id,
            "objective": objective,
            "auto_approve": auto_approve,
            "status": RunStatus.CREATED.value,
            "iteration": 0,
            "max_iterations": max_iterations,
            "approval": ApprovalDecision.PENDING.value,
            "event_log": [],
        }
        config = {"configurable": {"thread_id": selected_run_id}}
        return self.graph.invoke(initial, config=config)

    def resume(self, run_id: str, decision: ApprovalDecision) -> WorkflowState:
        config = {"configurable": {"thread_id": run_id}}
        return self.graph.invoke(Command(resume=decision.value), config=config)
