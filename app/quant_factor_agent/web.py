from __future__ import annotations

import argparse
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from quant_factor_agent import __version__
from quant_factor_agent.cli import build_workflow, default_data_root
from quant_factor_agent.data import MarketDataRequest, scan_data_root
from quant_factor_agent.domain.schemas import ApprovalDecision, FactorImplementation
from quant_factor_agent.evaluation import FactorEvaluator, MultiFactorEngine
from quant_factor_agent.execution import OrderRequest, PaperBrokerAdapter
from quant_factor_agent.factors import FactorRuntime
from quant_factor_agent.llm import ModelProviderError, provider_from_env, provider_from_settings
from quant_factor_agent.retrieval import LocalKnowledgeBase
from quant_factor_agent.storage import ExperimentStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "web_static"


class KnowledgeIndexRequest(BaseModel):
    root: str | None = None
    limit: int | None = Field(default=None, ge=1)
    workers: int = Field(default=4, ge=1, le=8)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1_000)
    limit: int = Field(default=6, ge=1, le=20)


class ModelConfigRequest(BaseModel):
    provider: Literal["openai", "openai_compatible"] = "openai_compatible"
    api_key: str = Field(min_length=4)
    model: str = Field(min_length=2)
    base_url: str = "https://api.openai.com/v1"
    api_style: Literal["auto", "responses", "chat"] = "auto"
    test_connection: bool = True


class ResearchRequest(BaseModel):
    objective: str = Field(min_length=10, max_length=2_000)
    agent_mode: Literal["mock", "live"] = "mock"
    market: str = "CN"
    universe: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    frequency: Literal["daily", "weekly", "monthly"] = "monthly"
    holding_period: int = Field(default=5, ge=1, le=252)
    factor_type: str = "auto"
    data_provider: Literal["synthetic", "local_csmar_csv"] = "synthetic"
    use_local_reports: bool = True
    auto_approve: bool = True
    max_iterations: int = Field(default=2, ge=0, le=3)


class SaveFactorRequest(BaseModel):
    run_id: str


class MultiFactorRequest(BaseModel):
    factor_ids: list[str] = Field(min_length=2)
    method: Literal["equal_weight", "ic_weighted"] = "equal_weight"
    data_provider: Literal["synthetic", "local_csmar_csv"] = "synthetic"


class PriceUpdateRequest(BaseModel):
    prices: dict[str, float]


def _job(app: FastAPI, kind: str) -> tuple[str, dict[str, Any]]:
    job_id = uuid4().hex
    payload: dict[str, Any] = {"job_id": job_id, "kind": kind, "status": "queued", "result": None}
    with app.state.job_lock:
        app.state.jobs[job_id] = payload
    return job_id, payload


def _set_job(app: FastAPI, job_id: str, **updates: Any) -> None:
    with app.state.job_lock:
        app.state.jobs[job_id].update(updates)


def _research_summary(state: dict[str, Any], workflow: Any) -> dict[str, Any]:
    evidence = state.get("evidence", [])
    return {
        "run_id": state["run_id"],
        "status": state["status"],
        "approval": state["approval"],
        "iteration": state["iteration"],
        "hypothesis": state.get("hypothesis"),
        "implementation": state.get("implementation"),
        "evaluation": state.get("evaluation"),
        "evaluations": state.get("evaluations"),
        "feedback": state.get("feedback"),
        "citations": [
            {
                "evidence_id": item.get("evidence_id"),
                "title": item.get("title"),
                "path": item.get("source_path"),
                "page": item.get("page"),
                "chunk_id": item.get("chunk_id"),
                "score": item.get("rerank_score") or item.get("lexical_score"),
            }
            for item in evidence
        ],
        "data_context": state.get("data_context", []),
        "event_log": state.get("event_log", []),
        "retrieval_mode": getattr(workflow, "retrieval_mode", "unknown"),
        "market_data_provider": getattr(workflow, "market_data_provider", "unknown"),
        "interrupts": [item.value for item in state.get("__interrupt__", ())],
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="Quantary API",
        version=__version__,
        description="Evidence-grounded, leakage-aware AI quantitative factor research.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.state.jobs = {}
    app.state.job_lock = threading.Lock()
    app.state.model_provider = None
    app.state.model_label = None
    if os.getenv("LLM_PROVIDER", "mock").strip().lower() not in {"", "mock", "offline"}:
        try:
            app.state.model_provider = provider_from_env()
            app.state.model_label = (
                f"{app.state.model_provider.provider_name}:{app.state.model_provider.model_name}"
            )
        except ModelProviderError:
            # Health/config endpoints expose the unconfigured state without
            # preventing the mock product from starting.
            pass
    app.state.data_root = default_data_root()
    app.state.knowledge = LocalKnowledgeBase(PROJECT_ROOT / "data" / "knowledge.db")
    app.state.store = ExperimentStore(PROJECT_ROOT / "data" / "experiments.db")
    app.state.paper = PaperBrokerAdapter(PROJECT_ROOT / "data" / "paper_trading.db")

    @app.middleware("http")
    async def prevent_stale_frontend_assets(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "quant-factor-rd-agent",
            "version": __version__,
            "model_configured": app.state.model_provider is not None,
            "model": app.state.model_label,
            "knowledge": app.state.knowledge.stats(),
        }

    @app.get("/api/data/inventory")
    def inventory() -> dict[str, Any]:
        return scan_data_root(app.state.data_root)

    @app.get("/api/knowledge/stats")
    def knowledge_stats() -> dict[str, Any]:
        return {
            **app.state.knowledge.stats(),
            "root": str(app.state.data_root),
            "documents": app.state.knowledge.list_documents(limit=20),
        }

    @app.post("/api/knowledge/index", status_code=202)
    def index_knowledge(request: KnowledgeIndexRequest) -> dict[str, str]:
        root = Path(request.root) if request.root else app.state.data_root
        job_id, _ = _job(app, "knowledge_index")

        def worker() -> None:
            _set_job(app, job_id, status="running")
            try:
                report = app.state.knowledge.index_directory(
                    root, limit=request.limit, workers=request.workers
                )
                _set_job(app, job_id, status="completed", result=asdict(report))
            except Exception as exc:
                _set_job(app, job_id, status="failed", error=str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id, "status": "queued"}

    @app.post("/api/knowledge/search")
    def search_knowledge(request: KnowledgeSearchRequest) -> dict[str, Any]:
        results = app.state.knowledge.search(request.query, limit=request.limit)
        return {"query": request.query, "results": [item.model_dump(mode="json") for item in results]}

    @app.post("/api/model/config")
    def configure_model(request: ModelConfigRequest) -> dict[str, Any]:
        try:
            provider = provider_from_settings(
                provider=request.provider,
                api_key=request.api_key,
                model=request.model,
                base_url=request.base_url,
                api_style=request.api_style,
            )
            health_result = provider.health_check() if request.test_connection else None  # type: ignore[attr-defined]
        except ModelProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.model_provider = provider
        app.state.model_label = f"{request.provider}:{request.model}"
        return {
            "configured": True,
            "provider": request.provider,
            "model": request.model,
            "health": health_result.model_dump() if health_result else None,
            "api_key_persisted": False,
        }

    @app.get("/api/model/config")
    def model_config() -> dict[str, Any]:
        return {"configured": app.state.model_provider is not None, "model": app.state.model_label,
                "api_key_persisted": False}

    @app.post("/api/research/runs", status_code=202)
    def start_research(request: ResearchRequest) -> dict[str, str]:
        if request.agent_mode == "live" and app.state.model_provider is None:
            raise HTTPException(status_code=400, detail="请先在 Connections 配置并测试模型")
        job_id, _ = _job(app, "research")

        def worker() -> None:
            _set_job(app, job_id, status="running")
            objective = (
                f"{request.objective}\n市场={request.market}; 持有期={request.holding_period}; "
                f"因子类型={request.factor_type}; 频率={request.frequency}."
            )
            try:
                market_request = MarketDataRequest(
                    market=request.market,
                    universe=request.universe,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    frequency=request.frequency,
                )
                with build_workflow(
                    PROJECT_ROOT,
                    mode=request.agent_mode,
                    data_provider=request.data_provider,
                    market_request=market_request,
                    use_local_reports=request.use_local_reports,
                    model_provider=app.state.model_provider,
                    holding_period=request.holding_period,
                ) as workflow:
                    state = workflow.run(
                        objective,
                        auto_approve=request.auto_approve,
                        max_iterations=request.max_iterations,
                    )
                    result = _research_summary(state, workflow)
                _set_job(app, job_id, status="completed", result=result)
            except Exception as exc:
                _set_job(app, job_id, status="failed", error=str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id, "status": "queued"}

    @app.post("/api/research/runs/{run_id}/approve")
    def approve_research(run_id: str, decision: ApprovalDecision) -> dict[str, Any]:
        try:
            with build_workflow(PROJECT_ROOT, model_provider=app.state.model_provider) as workflow:
                state = workflow.resume(run_id, decision)
                return _research_summary(state, workflow)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        with app.state.job_lock:
            job = app.state.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            return dict(job)

    @app.get("/api/runs")
    def list_runs(limit: int = 100) -> list[dict[str, Any]]:
        return app.state.store.list_runs(limit=max(1, min(limit, 500)))

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return app.state.store.get_run_bundle(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/factors")
    def save_factor(request: SaveFactorRequest) -> dict[str, Any]:
        try:
            bundle = app.state.store.get_run_bundle(request.run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if bundle["run"]["status"] != "accepted":
            raise HTTPException(status_code=400, detail="only accepted research can enter Factor Library")
        by_type = {item["event_type"]: item["payload"] for item in bundle["events"]}
        implementation = by_type.get("implementation_generated")
        hypothesis = by_type.get("hypothesis_proposed")
        evaluations = by_type.get("factor_evaluated")
        if not implementation or not hypothesis or not evaluations:
            raise HTTPException(status_code=400, detail="run does not contain a complete factor experiment")
        factor_id = implementation["spec"]["factor_id"]
        app.state.store.save_factor(
            factor_id=factor_id,
            name=implementation["spec"]["name"],
            source_run_id=request.run_id,
            hypothesis=hypothesis,
            implementation=implementation,
            evaluation=evaluations,
        )
        return {"saved": True, "factor_id": factor_id}

    @app.get("/api/factors")
    def list_factors() -> list[dict[str, Any]]:
        return app.state.store.list_factors()

    @app.post("/api/multifactor/analyze")
    def analyze_multifactor(request: MultiFactorRequest) -> dict[str, Any]:
        library = {item["factor_id"]: item for item in app.state.store.list_factors()}
        missing = [item for item in request.factor_ids if item not in library]
        if missing:
            raise HTTPException(status_code=404, detail=f"unknown factor ids: {missing}")
        try:
            with build_workflow(PROJECT_ROOT, mode="mock", data_provider=request.data_provider) as workflow:
                runtime = FactorRuntime()
                factor_frames: dict[str, pd.DataFrame] = {}
                for factor_id in request.factor_ids:
                    item = library[factor_id]
                    implementation = FactorImplementation.model_validate(item["implementation"])
                    factor_input: pd.DataFrame | dict[str, pd.DataFrame]
                    if implementation.spec.input_name == "market_data":
                        factor_input = workflow.dependencies.market_data or {}
                    else:
                        factor_input = workflow.dependencies.monthly_returns
                    label = str(item["name"])
                    if label in factor_frames:
                        label = f"{label}:{factor_id[:6]}"
                    factor_frames[label] = runtime.execute(implementation, factor_input)
                result = MultiFactorEngine().analyze(
                    factor_frames, workflow.dependencies.monthly_returns, method=request.method
                )
            result.pop("combined_factor", None)
            return result
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/learn/experiment")
    def learn_experiment() -> dict[str, Any]:
        with build_workflow(PROJECT_ROOT, mode="mock", data_provider="synthetic") as workflow:
            returns = workflow.dependencies.monthly_returns
            factor = (1.0 + returns.shift(1)).rolling(6, min_periods=6).apply(pd.Series.prod, raw=False) - 1.0
            evaluation = FactorEvaluator().evaluate(factor, returns, split="development")
        return {
            "lesson": "用过去 6 个月、且至少滞后 1 期的收益构造动量分数，预测当期横截面收益。",
            "formula": "momentum_t = product(1 + return[t-6:t-1]) - 1",
            "why_lag": "shift(1) 防止把形成期或未来信息泄漏进因子。",
            "evaluation": evaluation.model_dump(mode="json"),
            "data": "synthetic (教学默认；本地真实行情就绪后可切换同一 Quant Engine)",
        }

    @app.get("/api/paper/account")
    def paper_account() -> dict[str, object]:
        return app.state.paper.get_account()

    @app.get("/api/paper/positions")
    def paper_positions() -> list[dict[str, object]]:
        return app.state.paper.get_positions()

    @app.get("/api/paper/orders")
    def paper_orders() -> list[dict[str, object]]:
        return app.state.paper.get_orders()

    @app.post("/api/paper/prices")
    def paper_prices(request: PriceUpdateRequest) -> dict[str, object]:
        app.state.paper.update_market_prices(request.prices)
        return {"updated": len(request.prices), "live_market_data": False}

    @app.post("/api/paper/orders")
    def paper_order(request: OrderRequest) -> dict[str, object]:
        try:
            return app.state.paper.place_order(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/paper/orders/{order_id}/cancel")
    def cancel_paper_order(order_id: str) -> dict[str, object]:
        try:
            return app.state.paper.cancel_order(order_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.mount("/assets", StaticFiles(directory=STATIC_ROOT), name="assets")
    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Quant Factor R&D Agent web server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("quant_factor_agent.web:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
