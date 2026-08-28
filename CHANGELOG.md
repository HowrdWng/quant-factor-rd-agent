# Changelog

All notable changes to Quantary are documented here. The project follows semantic versioning while APIs remain
in research preview.

## [0.3.0] - 2026-08-28

### Added

- FastAPI product UI with Learn, AI Research, Runs, Factor Library, Multi-Factor, Backtest, Paper Trading,
  Knowledge & Data, and Connections surfaces.
- Incremental local PDF/Markdown/TXT knowledge indexing with citation lineage.
- Provider-neutral market data and broker adapter boundaries.
- In-memory browser model configuration and connection health check.
- OpenAI Responses and Chat Completions structured-output support.
- Docker Compose deployment, product documentation, security policy, and GitHub project templates.

### Changed

- Unified product identity and package version as Quantary v0.3.0.
- Streamlit moved out of core dependencies and retained only as an optional legacy UI.
- Research and Development prompts now separate raw factor scores from portfolio construction.

### Fixed

- Successful Responses API payloads containing `"error": null` are no longer treated as failures.
- Browser model setup no longer dereferences an expired DOM event target after an asynchronous request.
- Versioned frontend assets prevent stale JavaScript from surviving product updates.

## [0.2.0] - 2026-08-26

- Initial Research–Development workflow, local RAG, deterministic evaluation, factor library, multi-factor
  analysis, and paper broker preview.
