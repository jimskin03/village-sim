"""Append-only episodic/semantic memory and deterministic hybrid retrieval.

Records retain the event fields consumed by the viewer. Embeddings are optional
data supplied by a caller; retrieval never performs network calls. IDs come from
the simulation's event sequence, so summaries and episodes share one namespace.
"""

from __future__ import annotations

import copy
from bisect import insort
from collections import defaultdict
import heapq
import math
import re

_IMPORTANCE = {
    "promise": 0.95, "insight": 0.9, "trust": 0.88, "reflection": 0.85, "goal": 0.8,
    "supper": 0.75, "weather": 0.7, "festival": 0.7, "conversation": 0.65,
    "collapse": 1.0, "recovery": 0.9, "survival": 0.65,
    "work_summary": 0.5, "arrival": 0.4, "work": 0.3, "activity": 0.15,
    "visitor_witness": 0.75, "visitor_influence": 0.7, "visitor_speech": 0.6,
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
        type="semantic" if event["kind"] in ("reflection", "work_summary", "insight", "trust") else "episodic",
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
        self._words: list[set[str]] = []
        self._ids: dict[int, int] = {}
        self._postings: dict[str, set[int]] = defaultdict(set)
        self._importance: dict[float, list[tuple]] = defaultdict(list)
        self.last_candidates = 0

    def _sync(self):
        if len(self._words) > len(self.records):
            self.__init__(self.records)
        for index in range(len(self._words), len(self.records)):
            record = self.records[index]
            self._ids[record["id"]] = index
            words = _tokens(record["content"])
            self._words.append(words)
            for word in words:
                self._postings[word].add(index)
            insort(self._importance[record["importance"]], (record["timestamp"], record["id"], index))

    def get(self, identifier):
        self._sync()
        index = self._ids.get(identifier)
        return self.records[index] if index is not None else None

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
        if not limit:
            return []
        self._sync()
        tokens = _tokens(query)
        use_embedding = valid_embedding(query_embedding) and bool(embedding_model)
        candidates = set()
        for token in tokens:
            candidates.update(self._postings.get(token, ()))
        # A nonmatching record can win only through importance/recency. Within
        # each importance bucket the newest k dominate all older records.
        for entries in self._importance.values():
            candidates.update(entry[2] for entry in entries[-limit:])
        if use_embedding:
            candidates.update(index for index, record in enumerate(self.records)
                              if record.get("embedding_model") == embedding_model)
        self.last_candidates = len(candidates)

        def rank(index):
            record = self.records[index]
            words = self._words[index]
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

        selected = heapq.nlargest(limit, candidates, key=rank)
        return copy.deepcopy([self.records[index] for index in selected])


def memory_view(records: list[dict], *, limit: int = 12, characters: int = 6000) -> list[dict]:
    """Bound transmitted text and evidence without altering the saved archive."""
    result = []
    for record in records[:limit]:
        if characters <= 0:
            break
        item = {key: copy.deepcopy(value) for key, value in record.items()
                if key not in ("embedding", "embedding_model", "source_ids", "content", "text")}
        content = record["text"][:min(1200, characters)]
        item.update(text=content, content=content, source_ids=record.get("source_ids", [])[:16])
        characters -= len(content)
        result.append(item)
    return result
