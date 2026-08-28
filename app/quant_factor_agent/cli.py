from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from quant_factor_agent.agents import (
    LLMDevelopmentAgent,
    LLMResearchAgent,
    MockDevelopmentAgent,
    MockResearchAgent,
)
from quant_factor_agent.data import (
    DataCatalogTool,
    LocalCsmarCsvProvider,
    MarketDataRequest,
    SyntheticMarketDataProvider,
    scan_data_root,
)
from quant_factor_agent.demo import demo_evidence
from quant_factor_agent.domain.schemas import ApprovalDecision
from quant_factor_agent.evaluation import FactorEvaluator
from quant_factor_agent.factors import FactorRuntime
from quant_factor_agent.llm import ModelProvider, ModelProviderError, provider_from_env
from quant_factor_agent.retrieval import InMemoryRetriever, KnowledgeBaseRetriever, LocalKnowledgeBase
from quant_factor_agent.storage import ExperimentStore
from quant_factor_agent.workflow import ResearchDevelopmentWorkflow, WorkflowDependencies


def agent_mode() -> str:
    return os.getenv("AGENT_MODE", "mock").strip().lower() or "mock"


def default_data_root() -> Path:
    configured = os.getenv("QUANT_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    windows_default = Path("E:/fin_data")
    return windows_default if windows_default.exists() else Path.cwd() / "workspace"


def build_workflow(
    root: Path,
    *,
    mode: str | None = None,
    data_provider: str | None = None,
    market_request: MarketDataRequest | None = None,
    use_local_reports: bool = True,
    model_provider: ModelProvider | None = None,
    holding_period: int = 1,
) -> ResearchDevelopmentWorkflow:
    selected_mode = (mode or agent_mode()).strip().lower()
    if selected_mode == "live":
        provider = model_provider or provider_from_env()
        research_agent = LLMResearchAgent(provider)
        development_agent = LLMDevelopmentAgent(provider)
        metadata_mode = "live"
    elif selected_mode in {"mock", "demo", "offline"}:
        research_agent = MockResearchAgent()
        development_agent = MockDevelopmentAgent()
        metadata_mode = "mock"
    else:
        raise ValueError(f"Unsupported AGENT_MODE '{selected_mode}'")

    knowledge_base = LocalKnowledgeBase(root / "data" / "knowledge.db")
    if use_local_reports and knowledge_base.stats()["chunks"] > 0:
        retriever = KnowledgeBaseRetriever(knowledge_base, fallback=InMemoryRetriever(demo_evidence()))
        retrieval_mode = "local_knowledge_base"
    else:
        retriever = InMemoryRetriever(demo_evidence())
        retrieval_mode = "demo_memory"

    selected_provider = (data_provider or os.getenv("MARKET_DATA_PROVIDER", "synthetic")).lower()
    if selected_provider in {"local", "local_csmar", "local_csmar_csv"}:
        market_provider = LocalCsmarCsvProvider(default_data_root())
    else:
        market_provider = SyntheticMarketDataProvider()
    selected_request = market_request or MarketDataRequest(frequency="monthly")
    bundle = market_provider.load(selected_request)
    periods_per_year = {"daily": 252, "weekly": 52, "monthly": 12}[selected_request.frequency]

    dependencies = WorkflowDependencies(
        research_agent=research_agent,
        development_agent=development_agent,
        retriever=retriever,
        runtime=FactorRuntime(),
        evaluator=FactorEvaluator(
            periods_per_year=periods_per_year,
            forward_periods=holding_period,
        ),
        store=ExperimentStore(root / "data" / "experiments.db"),
        monthly_returns=bundle.returns,
        artifact_dir=root / "artifacts",
        market_data=bundle.frames,
        data_catalog=DataCatalogTool(default_data_root()),
        checkpoint_path=root / "data" / "checkpoints.db",
        mode=metadata_mode,
    )
    workflow = ResearchDevelopmentWorkflow(dependencies)
    workflow.mode = metadata_mode  # type: ignore[attr-defined]
    workflow.retrieval_mode = retrieval_mode  # type: ignore[attr-defined]
    workflow.market_data_provider = bundle.provider  # type: ignore[attr-defined]
    return workflow


def build_demo(root: Path) -> ResearchDevelopmentWorkflow:
    return build_workflow(root, mode="mock")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quant Factor Research-Development Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo_parser = subparsers.add_parser("demo", help="Run the deterministic offline workflow")
    demo_parser.add_argument(
        "--objective",
        default="Research a robust cross-sectional momentum factor",
    )
    demo_parser.add_argument("--auto-approve", action="store_true")
    demo_parser.add_argument("--max-iterations", type=int, default=2)
    demo_parser.add_argument("--mode", choices=["mock", "live"], default=None)
    approve_parser = subparsers.add_parser("approve", help="Resume a paused local demo run")
    approve_parser.add_argument("run_id")
    approve_parser.add_argument(
        "--decision",
        choices=[ApprovalDecision.APPROVED.value, ApprovalDecision.REJECTED.value],
        default=ApprovalDecision.APPROVED.value,
    )
    subparsers.add_parser("test-llm", help="Check live LLM credentials and structured JSON output")
    scan_parser = subparsers.add_parser("scan-data", help="Inspect local reports and market data")
    scan_parser.add_argument("--root", type=Path, default=default_data_root())
    index_parser = subparsers.add_parser("index-rag", help="Index local PDF/Markdown/TXT reports")
    index_parser.add_argument("--root", type=Path, default=default_data_root())
    index_parser.add_argument("--limit", type=int, default=None)
    index_parser.add_argument("--workers", type=int, default=4)
    watch_parser = subparsers.add_parser(
        "watch-rag", help="Continuously add newly completed local reports to RAG"
    )
    watch_parser.add_argument("--root", type=Path, default=default_data_root())
    watch_parser.add_argument("--workers", type=int, default=4)
    watch_parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    if args.command == "scan-data":
        print(json.dumps(scan_data_root(args.root), ensure_ascii=False, indent=2))
        return
    if args.command == "index-rag":
        report = LocalKnowledgeBase(root / "data" / "knowledge.db").index_directory(
            args.root, limit=args.limit, workers=args.workers
        )
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
        return
    if args.command == "watch-rag":
        knowledge = LocalKnowledgeBase(root / "data" / "knowledge.db")
        try:
            while True:
                report = knowledge.index_directory(args.root, workers=args.workers)
                print(json.dumps(report.__dict__, ensure_ascii=False), flush=True)
                time.sleep(max(10, args.interval))
        except KeyboardInterrupt:
            return
    if args.command == "test-llm":
        try:
            provider = provider_from_env()
            health = provider.health_check()  # type: ignore[attr-defined]
        except ModelProviderError as exc:
            raise SystemExit(f"LLM check failed: {exc}") from exc
        print(
            json.dumps(
                {
                    "status": health.status,
                    "message": health.message,
                    "provider": provider.provider_name,
                    "model": provider.model_name,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    with build_workflow(root, mode=getattr(args, "mode", None)) as workflow:
        if args.command == "demo":
            result = workflow.run(
                args.objective,
                auto_approve=args.auto_approve,
                max_iterations=args.max_iterations,
            )
        else:
            result = workflow.resume(args.run_id, ApprovalDecision(args.decision))
        summary = {
            "run_id": result["run_id"],
            "status": result["status"],
            "approval": result["approval"],
            "iteration": result["iteration"],
            "hypothesis": result.get("hypothesis", {}).get("title"),
            "factor": result.get("implementation", {}).get("spec", {}).get("name"),
            "evaluation": result.get("evaluation"),
            "evaluations": result.get("evaluations"),
            "feedback": result.get("feedback"),
            "event_log": result.get("event_log", []),
            "interrupts": [item.value for item in result.get("__interrupt__", ())],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
