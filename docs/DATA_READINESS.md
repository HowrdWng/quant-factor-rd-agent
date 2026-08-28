# Data readiness

Quantary keeps unstructured research knowledge and structured research data on separate paths.

## Routing rules

| Material | Route | Ready when |
| --- | --- | --- |
| PDF, Markdown, TXT, research notes | `LocalKnowledgeBase` | Complete file, extractable text, source metadata retained |
| Scanned PDF | OCR before retrieval | OCR quality sampled and page lineage retained |
| OHLCV and returns | `MarketDataProvider` | Point-in-time symbols/dates, adjustment method documented |
| Corporate actions | `MarketDataProvider` | Splits, dividends, delistings, and symbol changes reconciled |
| Fundamentals | `MarketDataProvider` | Aligned by public disclosure timestamp, not only period end |
| Universe membership | `MarketDataProvider` | Historical membership prevents survivorship bias |
| Partial downloads | Neither | File completes and passes integrity checks |

Structured market rows should not be embedded into RAG. The Research Agent can inspect data catalogs, but the
Quant Engine must load and calculate from aligned DataFrames.

## Minimum viable backtest dataset

- Stable security identifier and exchange/market.
- Trading calendar and timestamp convention.
- Open, high, low, close, volume, and preferably amount/value traded.
- Total-return or clearly documented price-adjustment fields.
- Corporate actions, delistings, and historical universe membership.
- Enough cross-sectional breadth and time coverage for development, validation, walk-forward, and holdout.
- Missingness, suspensions, limit moves, and stale-price behavior documented.

Fundamental factors additionally require statement version/restatement handling and public disclosure dates.

## Readiness sequence

1. Run `quant-factor-agent scan-data` and review incomplete files and missing categories.
2. Index only complete research documents with `quant-factor-agent index-rag`.
3. Validate a small market-data sample through the chosen provider.
4. Confirm timestamps, adjustments, and universe membership before accepting any performance result.
5. Compare a known baseline factor against an independent implementation.
6. Keep synthetic results visibly labeled; never present them as empirical market evidence.
