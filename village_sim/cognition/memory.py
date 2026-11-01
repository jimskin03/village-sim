"""Append-only episodic/semantic memory and deterministic hybrid retrieval.

Records retain the event fields consumed by the viewer. Embeddings are optional
data supplied by a caller; retrieval never performs network calls. IDs come from
the simulation's event sequence, so summaries and episodes share one namespace.
"""

from __future__ import annotations

import copy
import math
import re

_IMPORTANCE = {
    "promise": 0.95, "reflection": 0.85, "goal": 0.8, "supper": 0.75,
    "weather": 0.7, "festival": 0.7, "conversation": 0.65,
    "work_summary": 0.5, "arrival": 0.4, "work": 0.3, "activity": 0.15,
}
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have i in is it of on or that the "
    "their this to was we were will with".split()
)


def _tokens(text: str) -> set[str]:
    return {word.removesuffix("s") for word in re.findall(r"\w+", text.casefold())
            if word not in _STOPWORDS}


def valid_embedding(value) -> bool:
    return (isinstance(value, list) and 0 < len(value) <= 16384
            and all(type(number) in (int, float) and abs(number) <= 1.7976931348623157e308
                    for number in value))


def make_memory(event: dict, *, importance: float | None = None,
                embedding: list[float] | None = None,
                embedding_model: str | None = None) -> dict:
    """Enrich an event without changing its ID, content, or occurrence time."""
    record = copy.deepcopy(event)
    record.update(
        content=event["text"],
        timestamp=event["timestamp"],
        type="semantic" if event["kind"] in ("reflection", "work_summary") else "episodic",
        importance=_IMPORTANCE.get(event["kind"], 0.4) if importance is None else importance,
        source_ids=list(event.get("source_ids", [])),
        embedding=copy.deepcopy(embedding),
        embedding_model=embedding_model,
    )
    validate_memory(record)
    return record


def validate_memory(record: dict):
    """Validate cognitive fields in addition to the checkpoint's event fields."""
    importance = record.get("importance")
    if (record.get("type") not in ("episodic", "semantic")
            or not isinstance(record.get("content"), str)
            or record["content"] != record.get("text")
            or type(record.get("timestamp")) is not int or record["timestamp"] < 0
            or type(importance) not in (int, float)
            or not 0 <= importance <= 1
            or not isinstance(record.get("source_ids"), list)
            or any(type(source) is not int or source < 0 for source in record["source_ids"])):
        raise ValueError("Invalid memory metadata")
    embedding, model = record.get("embedding"), record.get("embedding_model")
    if ((embedding is None and model is not None)
            or (embedding is not None and (not valid_embedding(embedding)
                or not isinstance(model, str) or not 1 <= len(model) <= 200))):
        raise ValueError("Invalid memory embedding")


def _cosine(first, second):
    # Normalize by maximum magnitude first, avoiding overflow for valid floats.
    first_scale, second_scale = max(map(abs, first)), max(map(abs, second))
    if not first_scale or not second_scale:
        return None
    first = [value / first_scale for value in first]
    second = [value / second_scale for value in second]
    score = sum(a * b for a, b in zip(first, second)) / math.sqrt(
        sum(a * a for a in first) * sum(b * b for b in second)
    )
    return max(0.0, min(1.0, score))


class MemoryStore:
    """A lightweight view of a resident's serialized memory list."""

    def __init__(self, records: list[dict]):
        self.records = records

    def add(self, event: dict, **metadata) -> dict:
        record = make_memory(event, **metadata)
        self.records.append(record)
        return record

    def retrieve(self, query: str, *, now: int, limit: int = 12,
                 query_embedding: list[float] | None = None,
                 embedding_model: str | None = None) -> list[dict]:
        """Rank by relevance (60%), importance (25%), recency (15%).

        Relevance blends cosine similarity (75%) and lexical overlap (25%) for
        compatible nonzero vectors; otherwise it uses lexical overlap alone.
        Recency has a one-game-day half-life. Reads never alter saved records.
        """
        if type(limit) is not int or limit < 0:
            raise ValueError("Memory limit must be a nonnegative integer")
        tokens = _tokens(query)
        use_embedding = valid_embedding(query_embedding) and bool(embedding_model)

        def rank(record):
            words = _tokens(record.get("content", record["text"]))
            lexical = len(tokens & words) / math.sqrt(len(tokens) * len(words)) if tokens and words else 0
            relevance = lexical
            vector = record.get("embedding")
            if (use_embedding and record.get("embedding_model") == embedding_model
                    and valid_embedding(vector) and len(vector) == len(query_embedding)):
                similarity = _cosine(query_embedding, vector)
                if similarity is not None:
                    relevance = 0.75 * similarity + 0.25 * lexical
            age = max(0, now - record["timestamp"])
            recency = 0.5 ** (age / 1440)
            score = 0.6 * relevance + 0.25 * record["importance"] + 0.15 * recency
            return score, record["timestamp"], record["id"]

        return copy.deepcopy(sorted(self.records, key=rank, reverse=True)[:limit])
