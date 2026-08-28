from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class DataCatalogTool:
    """Read-only field discovery over local CSMAR metadata.

    This is a structured-data tool, not a vector store. It tells the RD Agent
    what can be calculated and whether the corresponding exported file exists.
    """

    ALIASES = {
        "volume": ("volume", "成交量", "交易股数", "换手率"),
        "momentum": ("动量", "return", "收益率", "回报率", "涨跌幅", "close", "收盘"),
        "volatility": ("波动", "high", "low", "最高", "最低", "收益率", "回报率"),
        "value": ("价值", "市值", "总资产", "净资产", "账面", "marketvalue"),
        "quality": ("质量", "净利润", "营业收入", "现金流", "资产负债"),
        "corporate_action": ("复权", "分红", "分配", "股本变动"),
    }

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        path = self.root / "csmar_tables.json"
        try:
            self.tables: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.tables = []

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        query_lower = query.lower()
        terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]{2,}", query_lower))
        for canonical, aliases in self.ALIASES.items():
            if canonical in query_lower or any(alias.lower() in query_lower for alias in aliases):
                terms.update(alias.lower() for alias in aliases)
        export_root = self.root / "csmar_exports"
        scored: list[tuple[int, dict[str, Any]]] = []
        for table in self.tables:
            columns = table.get("columns") or []
            searchable = " ".join(
                [
                    str(table.get("table_name", "")),
                    str(table.get("table_code", "")),
                    str(table.get("chinese_name", "")),
                    *(f"{column.get('name', '')} {column.get('label', '')}" for column in columns),
                ]
            ).lower()
            matched = [term for term in terms if term in searchable]
            if not matched:
                continue
            table_name = str(table.get("table_name", "")).lower()
            score = len(matched) + (3 if table_name.startswith("trd_") else 0)
            relevant_columns = [
                {"name": column.get("name"), "label": column.get("label")}
                for column in columns
                if any(
                    term in f"{column.get('name', '')} {column.get('label', '')}".lower()
                    for term in terms
                )
            ][:20]
            scored.append(
                (
                    score,
                    {
                        "table": table_name,
                        "title": table.get("chinese_name"),
                        "rows": table.get("data_rows"),
                        "estimated_mb": table.get("file_size_mb"),
                        "export_ready": (export_root / f"{table_name}.csv").exists(),
                        "matched_terms": sorted(matched),
                        "relevant_columns": relevant_columns,
                    },
                )
            )
        scored.sort(key=lambda item: (item[0], int(item[1].get("rows") or 0)), reverse=True)
        return [item for _, item in scored[: max(1, min(limit, 30))]]
