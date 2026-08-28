# Contributing to Quantary

Thanks for helping make quantitative research more reproducible and auditable.

## Development setup

```bash
python -m pip install -e ".[dev]"
ruff check app tests
pytest
python -m build
```

Mock mode is the default for tests and examples. Pull requests must not require an API key, broker account, or
private dataset.

## Design principles

1. Keep the existing Research–Development flow small and explicit; do not add an agent when a deterministic
   function or tool is sufficient.
2. LLM output never becomes a metric, order, or trusted executable artifact without deterministic validation.
3. Preserve point-in-time semantics and make missing data visible instead of silently substituting it.
4. Reports and papers belong in retrieval; market and financial tables belong behind data-provider interfaces.
5. Keep live execution outside the LLM boundary. Broker adapters must remain rule-gated and disabled by
   default.
6. Record adapted upstream source and licensing details in `THIRD_PARTY.md`.

## Pull requests

- Keep changes focused and include tests for behavior changes.
- Run Ruff, Pytest, and the package build locally.
- Do not commit `.env`, credentials, private reports, market datasets, SQLite state, generated artifacts, or
  upstream source snapshots.
- Explain any change to temporal splitting, leakage checks, factor acceptance gates, or broker permissions.
- UI changes should remain usable at common laptop widths and preserve the mock demo path.

## Reporting bugs and proposing features

Use the GitHub issue templates. Security issues should follow `SECURITY.md` and must not be posted publicly.
