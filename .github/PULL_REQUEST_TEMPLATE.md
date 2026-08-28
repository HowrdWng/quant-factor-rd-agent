## Summary

Describe the user/research problem and the smallest change that solves it.

## Validation

- [ ] `ruff check app tests`
- [ ] `pytest`
- [ ] `python -m build`
- [ ] Mock mode works without private data or API keys

## Scientific and safety impact

- [ ] Temporal/leakage behavior is unchanged or explicitly tested
- [ ] Generated-code permissions are unchanged or explicitly reviewed
- [ ] Broker/execution permissions are unchanged or explicitly reviewed
- [ ] No credentials, private datasets, reports, SQLite state, or generated artifacts are included

## Screenshots

Include before/after images for user-facing changes.
