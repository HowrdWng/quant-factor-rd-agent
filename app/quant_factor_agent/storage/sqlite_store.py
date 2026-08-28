from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from quant_factor_agent.domain.schemas import RunRecord, RunStatus


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class ExperimentStore:
    """SQLite experiment recorder inspired by Qlib's Recorder abstraction.

    The store owns business-level run, event, parameter, metric, and artifact
    metadata. LangGraph checkpoints remain a separate concern so either layer can
    be replaced independently.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    max_iterations INTEGER NOT NULL,
                    approval TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS params (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, key)
                );

                CREATE TABLE IF NOT EXISTS metrics (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    step INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, step, key)
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, name)
                );

                CREATE TABLE IF NOT EXISTS factor_library (
                    factor_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
                    hypothesis_json TEXT NOT NULL,
                    implementation_json TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                """
            )

    def create_run(self, record: RunRecord) -> None:
        payload = record.model_dump(mode="json")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, objective, status, iteration, max_iterations,
                    approval, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.objective,
                    record.status.value,
                    record.iteration,
                    record.max_iterations,
                    record.approval.value,
                    payload["created_at"],
                    payload["updated_at"],
                    json.dumps(record.metadata, default=_json_default, sort_keys=True),
                ),
            )

    def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        iteration: int | None = None,
        approval: str | None = None,
    ) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status.value, datetime.now(UTC).isoformat()]
        if iteration is not None:
            assignments.append("iteration = ?")
            values.append(iteration)
        if approval is not None:
            assignments.append("approval = ?")
            values.append(approval)
        values.append(run_id)
        with self.connect() as conn:
            result = conn.execute(
                f"UPDATE runs SET {', '.join(assignments)} WHERE run_id = ?",  # noqa: S608
                values,
            )
            if result.rowcount != 1:
                raise KeyError(f"Unknown run_id: {run_id}")

    def log_event(self, run_id: str, event_type: str, payload: Any) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            conn.execute(
                """
                INSERT INTO events (run_id, sequence, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sequence,
                    event_type,
                    json.dumps(payload, default=_json_default, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return sequence

    def log_params(self, run_id: str, params: Mapping[str, Any]) -> None:
        rows = [
            (run_id, key, json.dumps(value, default=_json_default, sort_keys=True))
            for key, value in params.items()
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO params (run_id, key, value_json) VALUES (?, ?, ?)",
                rows,
            )

    def log_metrics(self, run_id: str, metrics: Mapping[str, float], step: int = 0) -> None:
        now = datetime.now(UTC).isoformat()
        rows = [(run_id, step, key, float(value), now) for key, value in metrics.items()]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO metrics (run_id, step, key, value, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def log_artifact(
        self,
        run_id: str,
        name: str,
        path: str | Path,
        *,
        sha256: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (run_id, name, path, sha256, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    name,
                    str(path),
                    sha256,
                    json.dumps(dict(metadata or {}), default=_json_default, sort_keys=True),
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_runs(self, *, status: RunStatus | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        values: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            values.append(status.value)
        query += " ORDER BY updated_at DESC LIMIT ?"
        values.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, values).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            output.append(item)
        return output

    def get_metrics(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE run_id = ? ORDER BY step, key",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            output.append(item)
        return output

    def save_factor(
        self,
        *,
        factor_id: str,
        name: str,
        source_run_id: str | None,
        hypothesis: Mapping[str, Any],
        implementation: Mapping[str, Any],
        evaluation: Mapping[str, Any],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO factor_library (
                    factor_id, name, source_run_id, hypothesis_json,
                    implementation_json, evaluation_json, created_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    factor_id,
                    name,
                    source_run_id,
                    json.dumps(dict(hypothesis), default=_json_default, sort_keys=True),
                    json.dumps(dict(implementation), default=_json_default, sort_keys=True),
                    json.dumps(dict(evaluation), default=_json_default, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def list_factors(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM factor_library"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("hypothesis_json", "implementation_json", "evaluation_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            item["active"] = bool(item["active"])
            output.append(item)
        return output

    def get_run_bundle(self, run_id: str) -> dict[str, Any]:
        return {
            "run": self.get_run(run_id),
            "events": self.list_events(run_id),
            "metrics": self.get_metrics(run_id),
        }
