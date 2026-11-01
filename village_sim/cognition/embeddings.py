"""Optional, bounded per-connection embedding cache; credentials stay runtime-only."""

from collections import OrderedDict
from hashlib import sha256
from urllib.error import HTTPError

from .memory import valid_embedding


class EmbeddingCache:
    def __init__(self, capacity=512):
        self.capacity = capacity
        self.vectors = OrderedDict()
        self.model_space = None

    @staticmethod
    def space(config):
        return sha256((config.base_url + "\n" + config.embedding_model).encode()).hexdigest()

    def encode(self, config, texts):
        from ..providers import _complete

        space = self.space(config)
        if self.model_space != space:
            self.vectors.clear()
            self.model_space = space
        texts = list(dict.fromkeys(texts))
        if len(texts) > 25 or any(not isinstance(text, str) or len(text) > 6000 for text in texts):
            raise ValueError("Embedding batches are limited to 25 bounded texts")
        missing = [text for text in texts if text not in self.vectors]
        if missing:
            try:
                payload = _complete(config, {"model": config.embedding_model, "input": missing},
                                    endpoint="/embeddings", max_bytes=4 * 1024 * 1024)
            except HTTPError as error:
                error.close()
                raise ValueError("Embedding provider request failed") from None
            data = payload.get("data")
            if (not isinstance(data, list) or len(data) != len(missing)
                    or any(not isinstance(row, dict) or type(row.get("index")) is not int for row in data)
                    or {row["index"] for row in data} != set(range(len(missing)))):
                raise ValueError("Invalid embedding response")
            vectors = [row.get("embedding") for row in sorted(data, key=lambda row: row["index"])]
            if (not all(valid_embedding(vector) for vector in vectors)
                    or len({len(vector) for vector in vectors}) != 1):
                raise ValueError("Invalid embedding vectors")
            self.vectors.update(zip(missing, vectors))
        result = {text: self.vectors[text] for text in texts}
        for text in texts:
            self.vectors.move_to_end(text)
        while len(self.vectors) > self.capacity:
            self.vectors.popitem(last=False)
        return result
