from __future__ import annotations

import re
from collections import Counter
from typing import Protocol

from quant_factor_agent.domain.schemas import Evidence

TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]+|[\u4e00-\u9fff]+")


class Retriever(Protocol):
    def search(self, query: str, limit: int = 5) -> list[Evidence]: ...


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


class InMemoryRetriever:
    """Deterministic lexical retriever used before the hybrid RAG milestone."""

    def __init__(self, documents: list[Evidence]) -> None:
        self.documents = documents

    def search(self, query: str, limit: int = 5) -> list[Evidence]:
        query_counts = Counter(_tokens(query))
        scored: list[tuple[float, Evidence]] = []
        for document in self.documents:
            doc_counts = Counter(_tokens(f"{document.title} {document.text}"))
            overlap = sum(min(count, doc_counts[token]) for token, count in query_counts.items())
            normalized = overlap / max(1, sum(query_counts.values()))
            scored.append((normalized, document.model_copy(update={"lexical_score": normalized})))
        scored.sort(key=lambda item: (item[0], item[1].title), reverse=True)
        return [document for score, document in scored[:limit] if score > 0] or [
            document for _, document in scored[:1]
        ]
