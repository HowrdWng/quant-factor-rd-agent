from __future__ import annotations

import hashlib
import logging
import math
import re
import sqlite3
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from quant_factor_agent.domain.schemas import Evidence
from quant_factor_agent.retrieval.memory import Retriever

SUPPORTED_TEXT_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS | {".pdf"}
WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}|[\u4e00-\u9fff]+")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _document_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]


def _search_tokens(text: str) -> list[str]:
    """Tokenize English and Chinese without a heavyweight segmenter."""
    tokens: list[str] = []
    for match in WORD_PATTERN.findall(text.lower()):
        if "\u4e00" <= match[0] <= "\u9fff":
            tokens.append(match)
            tokens.extend(match[index : index + 2] for index in range(max(0, len(match) - 1)))
        else:
            tokens.append(match)
    return list(dict.fromkeys(token for token in tokens if token.strip()))


def _chunk_text(text: str, *, size: int = 1_300, overlap: int = 180) -> Iterator[str]:
    unicode_safe = text.encode("utf-8", errors="replace").decode("utf-8")
    cleaned = re.sub(r"[ \t]+", " ", unicode_safe.replace("\x00", " "))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + size)
        if end < len(cleaned):
            boundary = max(
                cleaned.rfind("\n", start + size // 2, end),
                cleaned.rfind("。", start, end),
            )
            if boundary > start:
                end = boundary + 1
        chunk = cleaned[start:end].strip()
        if len(chunk) >= 40:
            yield chunk
        if end >= len(cleaned):
            break
        start = max(start + 1, end - overlap)


def _read_text(path: Path) -> list[tuple[int | None, str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return [(None, raw.decode(encoding))]
        except UnicodeDecodeError:
            continue
    return [(None, raw.decode("utf-8", errors="replace"))]


def _read_pdf(path: Path) -> list[tuple[int | None, str]]:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path), strict=False)
    pages: list[tuple[int | None, str]] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages.append((number, text))
    return pages


@dataclass(frozen=True)
class IndexReport:
    discovered: int
    indexed: int
    skipped: int
    failed: int
    documents: int
    chunks: int
    needs_ocr: int


class LocalKnowledgeBase:
    """Resumable local report knowledge base backed by SQLite FTS5.

    CSV/Excel market and financial data deliberately stay outside RAG and are
    consumed by MarketDataProvider implementations.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    page INTEGER,
                    text TEXT NOT NULL,
                    UNIQUE(document_id, ordinal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    tokens, title, chunk_id UNINDEXED, document_id UNINDEXED,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )

    @staticmethod
    def discover(root: str | Path) -> list[Path]:
        base = Path(root)
        if not base.exists():
            return []
        return sorted(
            path
            for path in base.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def _is_unchanged(self, connection: sqlite3.Connection, path: Path) -> bool:
        stat = path.stat()
        row = connection.execute(
            "SELECT size_bytes, modified_ns, status FROM documents WHERE path = ?",
            (str(path.resolve()),),
        ).fetchone()
        return bool(
            row
            and row["size_bytes"] == stat.st_size
            and row["modified_ns"] == stat.st_mtime_ns
            and row["status"] in {"indexed", "needs_ocr", "failed"}
        )

    def index_directory(
        self,
        root: str | Path,
        *,
        limit: int | None = None,
        workers: int = 1,
    ) -> IndexReport:
        files = self.discover(root)
        if limit is not None:
            files = files[: max(0, limit)]
        indexed = skipped = failed = 0
        pending: list[Path] = []
        for path in files:
            with self.connect() as connection:
                if self._is_unchanged(connection, path):
                    skipped += 1
                    continue
            pending.append(path)
        if workers <= 1:
            for path in pending:
                try:
                    self.index_file(path)
                    indexed += 1
                except Exception as exc:
                    failed += 1
                    self._record_failure(path, str(exc))
        else:
            with ThreadPoolExecutor(max_workers=min(workers, 8)) as executor:
                futures = {executor.submit(self.index_file, path): path for path in pending}
                for future in as_completed(futures):
                    path = futures[future]
                    try:
                        future.result()
                        indexed += 1
                    except Exception as exc:
                        failed += 1
                        self._record_failure(path, str(exc))
        stats = self.stats()
        return IndexReport(
            discovered=len(files), indexed=indexed, skipped=skipped, failed=failed,
            documents=stats["documents"], chunks=stats["chunks"], needs_ocr=stats["needs_ocr"],
        )

    def _record_failure(self, path: Path, error: str) -> None:
        stat = path.stat()
        document_id = _document_id(path)
        with self.connect() as connection:
            connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, path, title, extension, size_bytes, modified_ns,
                    status, page_count, chunk_count, error, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'failed', 0, 0, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                    status='failed', chunk_count=0, error=excluded.error,
                    indexed_at=excluded.indexed_at
                """,
                (document_id, str(path.resolve()), path.stem, path.suffix.lower(),
                 stat.st_size, stat.st_mtime_ns, error[:2_000], _utc_now()),
            )

    def index_file(self, path: str | Path) -> int:
        source = Path(path)
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported knowledge document: {source.suffix}")
        stat = source.stat()
        document_id = _document_id(source)
        pages = _read_pdf(source) if source.suffix.lower() == ".pdf" else _read_text(source)
        chunks: list[tuple[str, int, int | None, str]] = []
        ordinal = 0
        for page, text in pages:
            for value in _chunk_text(text):
                ordinal += 1
                chunks.append((f"{document_id}:{ordinal}", ordinal, page, value))
        extracted_chars = sum(len(item[3]) for item in chunks)
        status = "indexed" if chunks and extracted_chars >= 80 else "needs_ocr"
        with self.connect() as connection:
            connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, path, title, extension, size_bytes, modified_ns,
                    status, page_count, chunk_count, error, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    path=excluded.path, title=excluded.title, extension=excluded.extension,
                    size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                    status=excluded.status, page_count=excluded.page_count,
                    chunk_count=excluded.chunk_count, error=NULL, indexed_at=excluded.indexed_at
                """,
                (document_id, str(source.resolve()), source.stem, source.suffix.lower(),
                 stat.st_size, stat.st_mtime_ns, status, len(pages), len(chunks), _utc_now()),
            )
            connection.executemany(
                "INSERT INTO chunks (chunk_id, document_id, ordinal, page, text) VALUES (?, ?, ?, ?, ?)",
                [(chunk_id, document_id, item_ordinal, page, text)
                 for chunk_id, item_ordinal, page, text in chunks],
            )
            connection.executemany(
                "INSERT INTO chunks_fts (tokens, title, chunk_id, document_id) VALUES (?, ?, ?, ?)",
                [(" ".join(_search_tokens(text)), " ".join(_search_tokens(source.stem)),
                  chunk_id, document_id) for chunk_id, _, _, text in chunks],
            )
        return len(chunks)

    def search(self, query: str, limit: int = 5) -> list[Evidence]:
        terms = _search_tokens(query)
        if not terms:
            return []
        expression = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:32])
        candidate_limit = max(20, min(limit * 10, 200))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.document_id, c.page, c.text,
                       d.title, d.path, bm25(chunks_fts, 1.0, 2.2) AS rank
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                JOIN documents d ON d.document_id = c.document_id
                WHERE chunks_fts MATCH ? ORDER BY rank ASC LIMIT ?
                """,
                (expression, candidate_limit),
            ).fetchall()
        weighted_total = sum(max(1, len(term)) for term in terms)
        rescored: list[tuple[float, Evidence]] = []
        for row in rows:
            body_tokens = set(_search_tokens(row["text"]))
            title_tokens = set(_search_tokens(row["title"]))
            body_overlap = sum(max(1, len(term)) for term in terms if term in body_tokens)
            title_overlap = sum(max(1, len(term)) for term in terms if term in title_tokens)
            coverage = body_overlap / max(1, weighted_total)
            title_coverage = title_overlap / max(1, weighted_total)
            bm25_strength = max(0.0, -float(row["rank"]))
            score = coverage * 0.70 + title_coverage * 0.20 + min(
                0.10, math.log1p(bm25_strength) / 50
            )
            rescored.append(
                (
                    score,
                    Evidence(
                        document_id=row["document_id"],
                        title=row["title"],
                        source_path=row["path"],
                        page=row["page"],
                        chunk_id=row["chunk_id"],
                        text=row["text"],
                        lexical_score=bm25_strength,
                        rerank_score=score,
                    ),
                )
            )
        rescored.sort(key=lambda item: item[0], reverse=True)
        selected: list[Evidence] = []
        per_document: dict[str, int] = {}
        for _, item in rescored:
            if per_document.get(item.document_id, 0) >= 2:
                continue
            selected.append(item)
            per_document[item.document_id] = per_document.get(item.document_id, 0) + 1
            if len(selected) >= max(1, min(limit, 20)):
                break
        return selected

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            document = connection.execute(
                """SELECT COUNT(*) AS documents,
                    SUM(CASE WHEN status='indexed' THEN 1 ELSE 0 END) AS indexed,
                    SUM(CASE WHEN status='needs_ocr' THEN 1 ELSE 0 END) AS needs_ocr,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                    FROM documents"""
            ).fetchone()
            chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
        return {"documents": int(document["documents"] or 0),
                "indexed": int(document["indexed"] or 0),
                "needs_ocr": int(document["needs_ocr"] or 0),
                "failed": int(document["failed"] or 0), "chunks": int(chunks or 0)}

    def list_documents(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT document_id, path, title, extension, size_bytes, status,
                    page_count, chunk_count, error, indexed_at
                    FROM documents ORDER BY indexed_at DESC LIMIT ?""",
                (max(1, min(limit, 1_000)),),
            ).fetchall()
        return [dict(row) for row in rows]


class KnowledgeBaseRetriever:
    def __init__(self, knowledge_base: LocalKnowledgeBase, fallback: Retriever | None = None) -> None:
        self.knowledge_base = knowledge_base
        self.fallback = fallback

    def search(self, query: str, limit: int = 5) -> list[Evidence]:
        results = self.knowledge_base.search(query, limit=limit)
        if results or self.fallback is None:
            return results
        return self.fallback.search(query, limit=limit)


def citations(evidence: Iterable[Evidence]) -> list[dict[str, object]]:
    return [{"evidence_id": item.evidence_id, "title": item.title,
             "path": item.source_path, "page": item.page, "chunk_id": item.chunk_id,
             "score": item.rerank_score or item.lexical_score} for item in evidence]
