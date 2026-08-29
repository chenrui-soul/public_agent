from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx2
import pytest
from openai import AsyncOpenAI

from public_agent.knowledge import (
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
)


def embedding_response(
    inputs: list[str],
    *,
    indexes: list[int] | None = None,
    dimensions: int = 384,
) -> dict[str, object]:
    response_indexes = indexes if indexes is not None else list(range(len(inputs)))
    markers = {text: float(position + 1) for position, text in enumerate(inputs)}
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": [markers[inputs[index]]] * dimensions,
            }
            for index in response_indexes
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
    }


def openai_client(
    handler: Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]],
    *,
    max_retries: int = 0,
) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="test-key-not-a-real-secret",
        base_url="https://openai.test/v1",
        max_retries=max_retries,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_openai_embedding_provider_batches_and_restores_response_order() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        inputs = payload["input"]
        assert isinstance(inputs, list)
        return httpx2.Response(
            200,
            json=embedding_response(inputs, indexes=list(reversed(range(len(inputs))))),
        )

    client = openai_client(handler)
    provider = OpenAIEmbeddingProvider(client=client, batch_size=2)
    try:
        vectors = await provider.embed_many(("first", "second", "third"))
    finally:
        await client.close()

    assert [payload["model"] for payload in requests] == [
        "text-embedding-3-small",
        "text-embedding-3-small",
    ]
    assert [payload["dimensions"] for payload in requests] == [384, 384]
    assert [payload["encoding_format"] for payload in requests] == ["float", "float"]
    assert [payload["input"] for payload in requests] == [
        ["first", "second"],
        ["third"],
    ]
    assert [vector[0] for vector in vectors] == [1.0, 2.0, 1.0]
    assert all(len(vector) == 384 for vector in vectors)


@pytest.mark.asyncio
async def test_openai_sdk_retries_rate_limit_and_server_errors() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(
                429,
                headers={"retry-after": "0"},
                json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
            )
        if attempts == 2:
            return httpx2.Response(
                500,
                json={"error": {"message": "temporary", "type": "server_error"}},
            )
        payload = json.loads(request.content)
        return httpx2.Response(200, json=embedding_response(payload["input"]))

    client = openai_client(handler, max_retries=2)
    provider = OpenAIEmbeddingProvider(client=client)
    try:
        vector = await provider.embed("retry me")
    finally:
        await client.close()

    assert attempts == 3
    assert len(vector) == 384


@pytest.mark.asyncio
async def test_openai_embedding_provider_sanitizes_transport_errors() -> None:
    leaked_secret = "sk-secret-must-not-leak"

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout(f"upstream failed with {leaked_secret}")

    client = openai_client(handler)
    provider = OpenAIEmbeddingProvider(client=client)
    try:
        with pytest.raises(EmbeddingProviderError) as error:
            await provider.embed("safe error")
    finally:
        await client.close()

    assert str(error.value) == "OpenAI embedding request failed"
    assert leaked_secret not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("indexes", "dimensions", "message"),
    [
        ([0, 0], 384, "duplicate indexes"),
        ([0], 384, "response is incomplete"),
        ([0, 1], 3, "dimensions do not match"),
    ],
)
async def test_openai_embedding_provider_rejects_invalid_responses(
    indexes: list[int],
    dimensions: int,
    message: str,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        return httpx2.Response(
            200,
            json=embedding_response(
                payload["input"],
                indexes=indexes,
                dimensions=dimensions,
            ),
        )

    client = openai_client(handler)
    provider = OpenAIEmbeddingProvider(client=client)
    try:
        with pytest.raises(EmbeddingProviderError, match=message):
            await provider.embed_many(("one", "two"))
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_embedding_provider_rejects_blank_text_without_api_call() -> None:
    calls = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    client = openai_client(handler)
    provider = OpenAIEmbeddingProvider(client=client)
    try:
        with pytest.raises(ValueError, match="blank text"):
            await provider.embed("  \n")
    finally:
        await client.close()

    assert calls == 0


@pytest.mark.asyncio
async def test_openai_embedding_provider_sanitizes_non_numeric_vectors() -> None:
    leaked_value = "provider-secret-value"

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "index": 0,
                        "embedding": [leaked_value, *([0.0] * 383)],
                    }
                ],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    client = openai_client(handler)
    provider = OpenAIEmbeddingProvider(client=client)
    try:
        with pytest.raises(EmbeddingProviderError) as error:
            await provider.embed("safe response validation")
    finally:
        await client.close()

    assert leaked_value not in str(error.value)


def test_openai_embedding_provider_rejects_blank_api_key() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        OpenAIEmbeddingProvider(api_key="   ")


def test_openai_embedding_provider_requires_fixed_knowledge_dimensions() -> None:
    with pytest.raises(ValueError, match="384 dimensions"):
        OpenAIEmbeddingProvider(api_key="test-key", dimensions=1536)
