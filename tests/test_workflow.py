from pathlib import Path

from quant_factor_agent.agents import MockDevelopmentAgent, MockResearchAgent
from quant_factor_agent.demo import demo_evidence, synthetic_monthly_returns
from quant_factor_agent.domain.schemas import ApprovalDecision
from quant_factor_agent.evaluation import FactorEvaluator
from quant_factor_agent.factors import FactorRuntime
from quant_factor_agent.retrieval import InMemoryRetriever
from quant_factor_agent.storage import ExperimentStore
from quant_factor_agent.workflow import ResearchDevelopmentWorkflow, WorkflowDependencies


def workflow(tmp_path: Path) -> tuple[ResearchDevelopmentWorkflow, ExperimentStore]:
    store = ExperimentStore(tmp_path / "experiments.db")
    dependencies = WorkflowDependencies(
        research_agent=MockResearchAgent(),
        development_agent=MockDevelopmentAgent(),
        retriever=InMemoryRetriever(demo_evidence()),
        runtime=FactorRuntime(),
        evaluator=FactorEvaluator(),
        store=store,
        monthly_returns=synthetic_monthly_returns(),
        artifact_dir=tmp_path / "artifacts",
        checkpoint_path=tmp_path / "checkpoints.db",
    )
    return ResearchDevelopmentWorkflow(dependencies), store


def test_workflow_stops_at_human_approval(tmp_path) -> None:
    graph, store = workflow(tmp_path)
    try:
        result = graph.run("Research a robust momentum factor", auto_approve=False)
        assert result["status"] == "waiting_approval"
        assert result["approval"] == "pending"
        assert "implementation" not in result
        assert result["__interrupt__"][0].value["type"] == "factor_hypothesis_approval"
        assert store.get_run(result["run_id"])["status"] == "waiting_approval"

        resumed = graph.resume(result["run_id"], ApprovalDecision.APPROVED)
        assert resumed["approval"] == "approved"
        assert "implementation" in resumed
    finally:
        graph.close()


def test_workflow_runs_end_to_end_with_mock_approval(tmp_path) -> None:
    graph, store = workflow(tmp_path)
    try:
        result = graph.run("Research a robust momentum factor", auto_approve=True)
        assert result["status"] in {"accepted", "rejected"}
        assert result["evaluation"]["passed_leakage_checks"] is True
        events = store.list_events(result["run_id"])
        assert events[0]["event_type"] == "evidence_retrieved"
        assert events[-1]["event_type"] == "run_finalized"
    finally:
        graph.close()
