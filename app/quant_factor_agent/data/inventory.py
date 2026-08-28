from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

RAG_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}
STRUCTURED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet", ".feather"}
MARKET_TABLES = {
    "trd_fwardquotation",
    "trd_bwardquotation",
    "trd_dalyr",
    "trd_mnth",
}
CORPORATE_ACTION_TABLES = {"trd_adjustfactor", "trd_cptl", "trd_capchg"}
FUNDAMENTAL_TABLES = {"fs_combas", "fs_comins", "fs_comscfd", "fs_comscfi"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def _table_file_exists(root: Path, names: set[str]) -> dict[str, bool]:
    export_root = root / "csmar_exports"
    if not export_root.exists():
        return {name: False for name in sorted(names)}
    available = {path.stem.lower() for path in export_root.glob("*.csv") if path.stat().st_size > 0}
    return {name: name in available for name in sorted(names)}


def scan_data_root(root: str | Path) -> dict[str, Any]:
    base = Path(root)
    if not base.exists():
        return {
            "root": str(base),
            "exists": False,
            "summary": {},
            "readiness": {},
            "missing": ["data root does not exist"],
        }

    files = [path for path in base.rglob("*") if path.is_file()]
    extensions = Counter(path.suffix.lower() or "[none]" for path in files)
    bytes_by_extension: Counter[str] = Counter()
    for path in files:
        try:
            bytes_by_extension[path.suffix.lower() or "[none]"] += path.stat().st_size
        except OSError:
            pass

    reports = _read_json(base / "research_reports.json", [])
    tables = _read_json(base / "csmar_tables.json", [])
    complete_reports = [path for path in (base / "research_reports").glob("*.pdf")]
    partial_reports = [path for path in (base / "research_reports").glob("*.part")]
    actual_structured = [
        path for path in files if path.suffix.lower() in STRUCTURED_EXTENSIONS
        and path.name not in {"research_reports.csv"}
    ]

    market_files = _table_file_exists(base, MARKET_TABLES)
    action_files = _table_file_exists(base, CORPORATE_ACTION_TABLES)
    fundamental_files = _table_file_exists(base, FUNDAMENTAL_TABLES)
    market_ready = any(market_files.values())
    actions_ready = all(action_files.values())
    fundamentals_ready = any(fundamental_files.values())
    metadata_tables = {str(item.get("table_name", "")).lower() for item in tables}
    estimated_mb = sum(float(item.get("file_size_mb") or 0) for item in tables)
    estimated_rows = sum(int(item.get("data_rows") or 0) for item in tables)

    missing: list[str] = []
    if not market_ready:
        missing.append("实际 OHLCV/收益率 CSV 尚未就绪；目前只有 CSMAR 表结构元数据")
    if not actions_ready:
        missing.append("复权因子、分红分配、股本变动数据尚未全部落地")
    if not fundamentals_ready:
        missing.append("财务报表 CSV 尚未落地，基本面因子暂时只能看字段定义")
    if partial_reports:
        missing.append(f"研报下载仍在进行，存在 {len(partial_reports)} 个 .part 临时文件")

    return {
        "root": str(base.resolve()),
        "exists": True,
        "summary": {
            "total_files": len(files),
            "total_bytes": sum(bytes_by_extension.values()),
            "extensions": [
                {"extension": extension, "count": count, "bytes": bytes_by_extension[extension]}
                for extension, count in extensions.most_common()
            ],
            "report_catalog_count": len(reports),
            "downloaded_pdf_count": len(complete_reports),
            "partial_download_count": len(partial_reports),
            "table_catalog_count": len(tables),
            "table_catalog_rows": estimated_rows,
            "table_catalog_estimated_mb": round(estimated_mb, 2),
            "actual_structured_file_count": len(actual_structured),
        },
        "readiness": {
            "rag": {
                "ready": bool(complete_reports),
                "documents": len(complete_reports),
                "use": "PDF/Markdown/TXT 进入本地知识库，回答时返回文件与页码引用",
            },
            "factor_research": {
                "ready": market_ready,
                "market_files": market_files,
                "metadata_available": bool(metadata_tables & MARKET_TABLES),
            },
            "backtest": {
                "ready": market_ready,
                "corporate_actions_ready": actions_ready,
                "warning": None if actions_ready else "未齐复权/分红/股本变动时，长期收益需谨慎解释",
            },
            "fundamentals": {
                "ready": fundamentals_ready,
                "files": fundamental_files,
                "metadata_available": bool(metadata_tables & FUNDAMENTAL_TABLES),
            },
            "corporate_actions": {"ready": actions_ready, "files": action_files},
        },
        "classification": {
            "rag": [str(path) for path in files if path.suffix.lower() in RAG_EXTENSIONS][:200],
            "structured_data": [str(path) for path in actual_structured][:200],
            "metadata_only": [
                str(base / "csmar_tables.json"),
                str(base / "csmar_table_details"),
                str(base / "research_reports.json"),
            ],
            "temporarily_unused": [str(path) for path in partial_reports],
        },
        "missing": missing,
    }
