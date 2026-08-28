# Local data audit template

Local datasets are intentionally excluded from Git. Generate a current inventory on the deployment machine:

```bash
quant-factor-agent scan-data --root /path/to/quant-data
```

Save machine-specific findings in `docs/DATA_AUDIT.local.md`; this filename is ignored by Git. A useful audit
should record file counts and sizes, date coverage, market/universe identifiers, adjustment method, disclosure
timestamps, survivorship handling, incomplete downloads, and whether each dataset is suitable for retrieval or
calculation.

See [`DATA_READINESS.md`](DATA_READINESS.md) for the release-independent checklist.
