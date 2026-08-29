from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from openai import AsyncOpenAI, OpenAIError
from pydantic import SecretStr

from public_agent.knowledge.base import (
    KNOWLEDGE_EMBEDDING_DIMENSIONS,
    EmbeddingProfile,
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)


class EmbeddingProviderError(RuntimeError):
    """Safe external embedding failure without provider response details."""


class DeterministicHashEmbeddingProvider:
    """Offline embedding baseline for tests and local development.

    Production deployments can replace this provider without changing ingestion,
    retrieval, or runtime contracts.
    """

    def __init__(self, *, dimensions: int = KNOWLEDGE_EMBEDDING_DIMENSIONS) -> None:
        self._profile = EmbeddingProfile(
            name="deterministic-hash-v1",
            dimensions=dimensions,
        )

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, text: str) -> tuple[float, ...]:
        return (await self.embed_many((text,)))[0]

    async def embed_many(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        return tuple(_hash_embedding(text, dimensions=self.profile.dimensions) for text in texts)


class OpenAIEmbeddingProvider:
    """Production OpenAI embedding adapter with fixed dimensions and safe errors."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None = None,
        model: str = "text-embedding-3-small",
        dimensions: int = KNOWLEDGE_EMBEDDING_DIMENSIONS,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        batch_size: int = 128,
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI embedding model must not be blank")
        if dimensions != KNOWLEDGE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                "OpenAI knowledge embeddings must use "
                f"{KNOWLEDGE_EMBEDDING_DIMENSIONS} dimensions"
            )
        if timeout_seconds <= 0:
            raise ValueError("OpenAI embedding timeout must be positive")
        if not 0 <= max_retries <= 5:
            raise ValueError("OpenAI embedding retries must be between 0 and 5")
        if not 1 <= batch_size <= 2048:
            raise ValueError("OpenAI embedding batch size must be between 1 and 2048")
        if client is None and api_key is None:
            raise ValueError("OpenAI API key is required when no client is supplied")

        self._model = model.strip()
        self._profile = EmbeddingProfile(
            name=f"openai:{self._model}",
            dimensions=dimensions,
        )
        self._batch_size = batch_size
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            secret = (
                api_key.get_secret_value()
                if isinstance(api_key, SecretStr)
                else api_key
            )
            if not secret or not secret.strip():
                raise ValueError("OpenAI API key must not be blank")
            self._client = AsyncOpenAI(
                api_key=secret,
                base_url=base_url,
                organization=organization,
                project=project,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, text: str) -> tuple[float, ...]:
        return (await self.embed_many((text,)))[0]

    async def embed_many(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        normalized = tuple(_validated_text(text) for text in texts)
        if not normalized:
            return ()

        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(normalized), self._batch_size):
            batch = normalized[start : start + self._batch_size]
            vectors.extend(await self._embed_batch(batch))
        return tuple(vectors)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def _embed_batch(self, batch: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        try:
            response = await self._client.embeddings.create(
                input=list(batch),
                model=self._model,
                dimensions=self.profile.dimensions,
                encoding_format="float",
            )
        except OpenAIError as exc:
            status_code = getattr(exc, "status_code", None)
            suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
            raise EmbeddingProviderError(
                f"OpenAI embedding request failed{suffix}"
            ) from None

        indexed: dict[int, tuple[float, ...]] = {}
        for item in response.data:
            if item.index in indexed:
                raise EmbeddingProviderError("OpenAI embedding response has duplicate indexes")
            if not 0 <= item.index < len(batch):
                raise EmbeddingProviderError("OpenAI embedding response index is out of range")
            indexed[item.index] = _validated_vector(
                item.embedding,
                dimensions=self.profile.dimensions,
            )
        if len(indexed) != len(batch):
            raise EmbeddingProviderError("OpenAI embedding response is incomplete")
        return tuple(indexed[index] for index in range(len(batch)))


def _hash_embedding(text: str, *, dimensions: int) -> tuple[float, ...]:
    tokens = _tokens(text)
    if not tokens:
        raise ValueError("cannot embed blank text")

    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimensions
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("embedding normalization produced a zero vector")
    return tuple(value / norm for value in vector)


def _validated_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("embedding input must be a string")
    if not text.strip():
        raise ValueError("cannot embed blank text")
    return text


def _validated_vector(
    vector: Sequence[float],
    *,
    dimensions: int,
) -> tuple[float, ...]:
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError, OverflowError):
        raise EmbeddingProviderError("OpenAI embedding contains a non-numeric value") from None
    if len(values) != dimensions:
        raise EmbeddingProviderError(
            "OpenAI embedding dimensions do not match the configured profile"
        )
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingProviderError("OpenAI embedding contains a non-finite value")
    return values


def _tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.findall(text.lower()):
        tokens.append(match)
        if len(match) > 3 and match.isascii():
            tokens.extend(match[index : index + 3] for index in range(len(match) - 2))
    return tuple(tokens)
