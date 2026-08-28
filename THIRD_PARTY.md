# Third-party references

The repositories below are retained under `references/` for architecture and
compatibility research. Their presence does not make their source part of this
application. Each project remains governed by its own license.

| Project | Upstream | License | Intended use |
| --- | --- | --- | --- |
| Qlib | https://github.com/microsoft/qlib | MIT | Workflow, recorder, dataset, and optional adapter design |
| RD-Agent | https://github.com/microsoft/RD-Agent | MIT | Hypothesis/experiment/evaluation feedback-loop design |
| TradingAgents | https://github.com/TauricResearch/TradingAgents | Apache-2.0 | LangGraph state, structured decisions, checkpoint and memory patterns |
| FinRobot | https://github.com/AI4Finance-Foundation/FinRobot | Apache-2.0 | Financial RAG tools and coordinated research workflow patterns |

Source snapshots were downloaded from the official GitHub archive endpoints on
2026-08-25 because direct Git protocol access was unavailable in the local
terminal. Before a release, snapshots must be pinned to commit hashes and any
adapted source must be listed below with its original file and notice.

## Adapted source register

No verbatim third-party source has been copied into the application.

The following designs were reimplemented for this project's domain:

| Local component | Design reference | Relationship |
| --- | --- | --- |
| `workflow/state.py` | TradingAgents `agent_states.py` | Original factor-research state, following its explicit typed-state pattern |
| `workflow/graph.py` | TradingAgents `graph/setup.py` and `conditional_logic.py` | Original graph and routes, following its separated graph/router pattern |
| SQLite graph checkpoint | TradingAgents `graph/checkpointer.py` | Uses the same upstream `langgraph-checkpoint-sqlite` package directly |
| `storage/sqlite_store.py` | Qlib `workflow/recorder.py` | Original SQLite implementation of the recorder interface pattern |
| Hypothesis/feedback lifecycle | RD-Agent `core/experiment.py` and `core/evolving_framework.py` | Original Pydantic domain model inspired by its experiment trace |
| Retrieval boundary | FinRobot `functional/rag.py` and `ragquery.py` | Original retriever protocol exposed as an agent dependency |

Direct runtime dependencies keep their own packages and licenses; they are not
vendored into this repository. The application currently calls LangGraph and
LangGraph's SQLite checkpointer through their public APIs.
