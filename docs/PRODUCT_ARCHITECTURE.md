# Product architecture v0.3

The upgrade is intentionally incremental. The existing LangGraph RD flow, Pydantic contracts, AST policy,
temporal vault, evaluator, SQLite audit store and bounded feedback loop remain the core.

```text
Learn UI ───────────────┐
                       ├── Quant Engine ── metrics / backtest
Research Web UI ─ API ─┤
                       └── RD Agent
                            ├── LocalKnowledgeBase (PDF/MD/TXT only)
                            ├── hypothesis + citations
                            └── constrained factor code

Market CSV/API ─ MarketDataProvider ─ structured DataFrames ─ Quant Engine

Validated signal ─ RiskRules ─ BrokerAdapter ─ PaperBrokerAdapter
                                            └─ future live adapter (disabled in V1)
```

Important boundaries:

- Reports and notes are chunked and indexed in SQLite FTS5. Evidence keeps document path, page and chunk ID.
- OHLCV, financial statements and factor panels are not embedded. They are loaded and calculated through
  `MarketDataProvider`.
- Generated code can calculate signals but cannot access files, the network, processes or brokers.
- The execution layer accepts only validated signals/orders after deterministic risk rules. LLM objects never
  receive a `BrokerAdapter`.
- Learn and Research share the same evaluator and data-provider contracts.

## Deployment boundary

V0.3 is a local/self-hosted single-user product. Binding to `127.0.0.1` is the safe default. Before exposing it
to the public internet, deploy it behind HTTPS and an identity-aware reverse proxy, and replace the in-process
model credential with a per-user secret vault. Multi-tenant authentication and live broker authorization are
deliberately not faked in this research milestone.
