# Open-source adoption plan

This project is an original, compact Research–Development agent for quantitative
factor research. It intentionally studies and reuses proven ideas from mature
open-source projects instead of rebuilding every subsystem from first principles.

## Adoption rules

1. Third-party repositories stay under `references/` and are never imported by
   adding their source trees to `PYTHONPATH`.
2. Prefer stable public packages and documented interfaces over copied source.
3. When source is adapted, preserve the upstream copyright/license header and
   record the source file and commit/archive date in `THIRD_PARTY.md`.
4. Keep the application domain model, factor safety rules, backtest protocol,
   retrieval evaluation, and UI original to this project.
5. Generated factor code runs in an isolated experiment workspace and cannot
   modify the registered factor library without an explicit approval step.

## What we adopt

### Microsoft RD-Agent (MIT)

Reference areas:

- `rdagent/core/experiment.py`
- `rdagent/core/evolving_framework.py`
- `rdagent/scenarios/qlib/proposal/`
- `rdagent/scenarios/qlib/developer/`
- `rdagent/scenarios/qlib/experiment/`

Adopted ideas:

- Separate research/proposal from implementation/development.
- Treat a hypothesis, experiment, feedback, and evolving trace as first-class
  domain objects.
- Feed deterministic evaluator results into the next research iteration.
- Keep factor and model experiments behind a shared experiment protocol.

Our implementation will be smaller, Pydantic-first, LangGraph-native, and tied
to a leakage-aware walk-forward protocol. We will not vendor RD-Agent's full
Co-STEER, Qlib scenario, prompt collection, or Docker runtime.

### TradingAgents (Apache-2.0)

Reference areas:

- `tradingagents/agents/schemas.py`
- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/graph/setup.py`
- `tradingagents/graph/conditional_logic.py`
- `tradingagents/graph/checkpointer.py`
- `tradingagents/agents/utils/memory.py`

Adopted ideas:

- Typed structured output at important decision boundaries.
- Explicit graph state instead of unstructured agent chat history.
- Conditional routing with bounded retries.
- SQLite-backed resumable graph checkpoints.
- Persistent decision/outcome memory and provider-neutral LLM adapters.

Our graph models a factor research lifecycle rather than analyst debate or an
LLM-generated trade recommendation.

### Microsoft Qlib (MIT)

Reference areas:

- `examples/workflow_by_code.py`
- `qlib/workflow/recorder.py`
- `qlib/workflow/record_temp.py`
- `qlib/data/dataset/`
- `qlib/backtest/`

Adopted ideas:

- Dataset, model, signal, analysis, and portfolio records are separate stages.
- Experiments log parameters, metrics, artifacts, tags, and model objects.
- Signal analysis and portfolio analysis are reproducible record templates.
- Research workflows can be defined by configuration or code.

The first implementation uses a lightweight local recorder and the existing
monthly portfolio engine. Qlib will be an optional adapter, not a mandatory
runtime dependency.

### FinRobot (Apache-2.0)

Reference areas:

- `finrobot/functional/rag.py`
- `finrobot/functional/ragquery.py`
- `finrobot/agents/workflow.py`

Adopted ideas:

- Retrieval is exposed as an agent tool rather than embedded in prompts.
- A research assistant combines tool results with explicit role instructions.
- Multi-agent workflows have a coordinator and bounded hand-offs.

Our retrieval layer adds hybrid BM25/vector retrieval, metadata filters,
reranking, citation lineage, and retrieval evaluation. We will not depend on
FinRobot's AutoGen workflow or copy its notebook-oriented application code.

## Components that remain ours

- Factor hypothesis and evidence schemas.
- Human approval policy.
- Point-in-time and look-ahead leakage checks.
- Factor registry and generated-code contract.
- Purged walk-forward/locked-holdout protocol.
- Factor diagnostics, multiple-testing controls, and acceptance gates.
- Existing ETF/equity allocation integration.
- Product UI, Chinese/English reporting, and portfolio presentation.

## Initial build order

1. Domain schemas and SQLite experiment store.
2. Deterministic factor runner and leakage-aware evaluator.
3. LangGraph workflow with mock research/development agents.
4. Human approval and bounded repair loop.
5. Hybrid retrieval and citation evaluation.
6. Real LLM provider adapter.
7. Existing quant project and optional Qlib adapters.
