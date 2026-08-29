from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from public_agent.core.events import InMemoryEventSink
from public_agent.core.runtime import AgentRuntime
from public_agent.core.types import (
    AgentSpec,
    Message,
    MessageRole,
    ModelRequest,
    RunContext,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from public_agent.providers import ModelProviderError, OpenAIModelProvider
from public_agent.tools.base import FunctionTool, ToolContext
from public_agent.tools.registry import ToolRegistry


def openai_client(
    handler: Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]],
) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="test-key-not-a-real-secret",
        base_url="https://openai.test/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def response_body(
    output: list[dict[str, Any]],
    *,
    model: str = "gpt-5.6-terra",
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1.0,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": status,
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {
                "cached_tokens": 3,
                "cache_write_tokens": 1,
            },
            "output_tokens": 7,
            "output_tokens_details": {"reasoning_tokens": 2},
            "total_tokens": 17,
        },
    }


def output_message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": [],
            }
        ],
    }


def function_call(
    call_id: str,
    name: str,
    arguments: str,
) -> dict[str, Any]:
    return {
        "id": f"fc_{call_id}",
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def reasoning_item() -> dict[str, Any]:
    return {
        "id": "rs_test",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "opaque-reasoning-state",
        "status": "completed",
    }


def add_tool() -> ToolDefinition:
    return ToolDefinition(
        name="add_numbers",
        description="Add two numbers",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "number"}},
            "required": ["result"],
            "additionalProperties": False,
        },
    )


def subtract_tool() -> ToolDefinition:
    return ToolDefinition(
        name="subtract_numbers",
        description="Subtract two numbers",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    )


def basic_messages() -> tuple[Message, ...]:
    return (
        Message(role=MessageRole.SYSTEM, content="Use registered tools only."),
        Message(role=MessageRole.USER, content="What is 2 + 3?"),
    )


async def add_numbers(
    arguments: dict[str, Any],
    context: ToolContext,
) -> dict[str, float]:
    del context
    return {"result": float(arguments["a"]) + float(arguments["b"])}


@pytest.mark.asyncio
async def test_openai_provider_converts_text_response_and_usage() -> None:
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=response_body([output_message("Five")]))

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        response = await provider.complete(ModelRequest(messages=basic_messages()))
    finally:
        await client.close()

    assert response.content == "Five"
    assert response.tool_calls == ()
    assert response.model_name == "gpt-5.6-terra"
    assert response.usage == {
        "input_tokens": 10,
        "cached_input_tokens": 3,
        "cache_write_tokens": 1,
        "output_tokens": 7,
        "reasoning_tokens": 2,
        "total_tokens": 17,
    }
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["max_output_tokens"] == 4096
    assert payload["parallel_tool_calls"] is True
    assert payload["store"] is False
    assert payload["truncation"] == "disabled"
    assert payload["input"] == [
        {
            "type": "message",
            "role": "system",
            "content": "Use registered tools only.",
        },
        {
            "type": "message",
            "role": "user",
            "content": "What is 2 + 3?",
        },
    ]
    assert requests[0].headers["idempotency-key"].startswith("public-agent-")


@pytest.mark.asyncio
async def test_openai_provider_preserves_tool_schemas_and_parses_parallel_calls() -> None:
    tools = (add_tool(), subtract_tool())
    original_schemas = copy.deepcopy(
        [(tool.input_schema, tool.output_schema) for tool in tools]
    )
    request_payload: dict[str, Any] = {}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        request_payload.update(json.loads(request.content))
        return httpx2.Response(
            200,
            json=response_body(
                [
                    function_call("call_add", "add_numbers", '{"a":2,"b":3}'),
                    function_call(
                        "call_subtract",
                        "subtract_numbers",
                        '{"a":9,"b":4}',
                    ),
                ]
            ),
        )

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        response = await provider.complete(
            ModelRequest(messages=basic_messages(), tools=tools)
        )
    finally:
        await client.close()

    assert response.content is None
    assert response.tool_calls == (
        ToolCall(
            id="call_add",
            name="add_numbers",
            arguments={"a": 2, "b": 3},
        ),
        ToolCall(
            id="call_subtract",
            name="subtract_numbers",
            arguments={"a": 9, "b": 4},
        ),
    )
    assert [(tool.input_schema, tool.output_schema) for tool in tools] == original_schemas
    assert request_payload["tools"] == [
        {
            "type": "function",
            "name": "add_numbers",
            "description": "Add two numbers",
            "parameters": tools[0].input_schema,
            "output_schema": tools[0].output_schema,
            "strict": True,
        },
        {
            "type": "function",
            "name": "subtract_numbers",
            "description": "Subtract two numbers",
            "parameters": tools[1].input_schema,
            "strict": True,
        },
    ]


@pytest.mark.asyncio
async def test_openai_provider_replays_reasoning_calls_and_tool_outputs() -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx2.Response(
                200,
                json=response_body(
                    [
                        reasoning_item(),
                        function_call("call_add", "add_numbers", '{"a":2,"b":3}'),
                        function_call(
                            "call_subtract",
                            "subtract_numbers",
                            '{"a":9,"b":4}',
                        ),
                    ]
                ),
            )
        return httpx2.Response(200, json=response_body([output_message("5 and 5")]))

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    tools = (add_tool(), subtract_tool())
    try:
        first = await provider.complete(
            ModelRequest(messages=basic_messages(), tools=tools)
        )
        second = await provider.complete(
            ModelRequest(
                messages=(
                    *basic_messages(),
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=first.content or "",
                        tool_calls=first.tool_calls,
                        provider_state=first.provider_state,
                    ),
                    Message(
                        role=MessageRole.TOOL,
                        name="add_numbers",
                        tool_call_id="call_add",
                        content='{"success":true,"output":{"result":5}}',
                    ),
                    Message(
                        role=MessageRole.TOOL,
                        name="subtract_numbers",
                        tool_call_id="call_subtract",
                        content='{"success":true,"output":{"result":5}}',
                    ),
                ),
                tools=tools,
            )
        )
    finally:
        await client.close()

    assert second.content == "5 and 5"
    second_input = payloads[1]["input"]
    assert [item["type"] for item in second_input] == [
        "message",
        "message",
        "reasoning",
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
    ]
    assert second_input[2]["encrypted_content"] == "opaque-reasoning-state"
    assert second_input[5]["call_id"] == "call_add"
    assert second_input[6]["call_id"] == "call_subtract"


@pytest.mark.asyncio
async def test_openai_provider_runs_end_to_end_through_agent_runtime() -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx2.Response(
                200,
                json=response_body(
                    [function_call("call_add", "add_numbers", '{"a":2,"b":3}')]
                ),
            )
        return httpx2.Response(200, json=response_body([output_message("5")]))

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    registry = ToolRegistry()
    registry.register(FunctionTool(add_tool(), add_numbers))
    spec = AgentSpec(
        id="calculator",
        name="Calculator",
        version="1.0.0",
        instructions="Use the calculator tool.",
        memory_namespace="calculator",
        allowed_tools=("add_numbers",),
    )
    try:
        result = await AgentRuntime(model=provider, tools=registry).run(
            agent=spec,
            task="What is 2 + 3?",
            context=RunContext(tenant_id="tenant-a"),
        )
    finally:
        await client.close()

    assert result.status is RunStatus.SUCCEEDED
    assert result.output == "5"
    assert len(payloads) == 2
    assert [item["type"] for item in payloads[1]["input"][-2:]] == [
        "function_call",
        "function_call_output",
    ]


@pytest.mark.asyncio
async def test_openai_provider_keeps_concurrent_requests_isolated() -> None:
    seen_prompts: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        prompt = payload["input"][-1]["content"]
        seen_prompts.append(prompt)
        await asyncio.sleep(0)
        return httpx2.Response(200, json=response_body([output_message(prompt)]))

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        first, second = await asyncio.gather(
            provider.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="first"),)
                )
            ),
            provider.complete(
                ModelRequest(
                    messages=(Message(role=MessageRole.USER, content="second"),)
                )
            ),
        )
    finally:
        await client.close()

    assert {first.content, second.content} == {"first", "second"}
    assert set(seen_prompts) == {"first", "second"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "message"),
    [
        (
            [function_call("call_unknown", "unknown_tool", "{}")],
            "unknown tool",
        ),
        (
            [function_call("call_add", "add_numbers", "not-json")],
            "not valid JSON",
        ),
        (
            [function_call("call_add", "add_numbers", "[1,2]")],
            "must be a JSON object",
        ),
        (
            [
                function_call("call_add", "add_numbers", '{"a":1,"b":2}'),
                function_call("call_add", "add_numbers", '{"a":3,"b":4}'),
            ],
            "duplicate tool call id",
        ),
        ([reasoning_item()], "no usable output"),
        (
            [
                {
                    "id": "msg_refusal",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": "not available"}],
                }
            ],
            "unsupported message output",
        ),
    ],
)
async def test_openai_provider_rejects_invalid_model_outputs(
    output: list[dict[str, Any]],
    message: str,
) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=response_body(output))

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        with pytest.raises(ModelProviderError, match=message):
            await provider.complete(
                ModelRequest(messages=basic_messages(), tools=(add_tool(),))
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_out_of_order_tool_history_without_api_call() -> None:
    calls = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        with pytest.raises(ModelProviderError, match="out of order"):
            await provider.complete(
                ModelRequest(
                    messages=(
                        Message(role=MessageRole.SYSTEM, content="Use tools."),
                        Message(
                            role=MessageRole.TOOL,
                            name="add_numbers",
                            tool_call_id="call_add",
                            content="{}",
                        ),
                    ),
                    tools=(add_tool(),),
                )
            )
    finally:
        await client.close()

    assert calls == 0


@pytest.mark.asyncio
async def test_openai_provider_rejects_tampered_provider_state() -> None:
    attempts = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            200,
            json=response_body(
                [function_call("call_add", "add_numbers", '{"a":2,"b":3}')]
            ),
        )

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        response = await provider.complete(
            ModelRequest(messages=basic_messages(), tools=(add_tool(),))
        )
        tampered_state = copy.deepcopy(response.provider_state)
        tampered_state["openai"]["output"][0]["name"] = "unknown_tool"
        with pytest.raises(ModelProviderError, match="unknown tool"):
            await provider.complete(
                ModelRequest(
                    messages=(
                        *basic_messages(),
                        Message(
                            role=MessageRole.ASSISTANT,
                            content="",
                            tool_calls=response.tool_calls,
                            provider_state=tampered_state,
                        ),
                        Message(
                            role=MessageRole.TOOL,
                            name="add_numbers",
                            tool_call_id="call_add",
                            content="{}",
                        ),
                    ),
                    tools=(add_tool(),),
                )
            )
    finally:
        await client.close()

    assert attempts == 1


@pytest.mark.asyncio
async def test_openai_provider_rejects_duplicate_tool_definitions_without_api_call() -> None:
    attempts = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(500)

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client)
    try:
        with pytest.raises(ModelProviderError, match="duplicate tool names"):
            await provider.complete(
                ModelRequest(
                    messages=basic_messages(),
                    tools=(add_tool(), add_tool()),
                )
            )
    finally:
        await client.close()

    assert attempts == 0


@pytest.mark.asyncio
async def test_openai_provider_retries_only_transient_statuses_with_same_key() -> None:
    attempts = 0
    idempotency_keys: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        idempotency_keys.append(request.headers["idempotency-key"])
        if attempts == 1:
            return httpx2.Response(
                429,
                headers={"retry-after": "0"},
                json={"error": {"message": "rate limited", "type": "rate_limit"}},
            )
        if attempts == 2:
            return httpx2.Response(
                500,
                json={"error": {"message": "temporary", "type": "server_error"}},
            )
        return httpx2.Response(200, json=response_body([output_message("ok")]))

    client = openai_client(handler)
    provider = OpenAIModelProvider(
        client=client,
        max_retries=2,
        retry_backoff_seconds=0,
    )
    try:
        response = await provider.complete(ModelRequest(messages=basic_messages()))
    finally:
        await client.close()

    assert response.content == "ok"
    assert attempts == 3
    assert len(set(idempotency_keys)) == 1


@pytest.mark.asyncio
async def test_openai_provider_does_not_retry_400_or_leak_provider_error() -> None:
    attempts = 0
    leaked_secret = "sk-provider-secret-must-not-leak"

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            400,
            json={
                "error": {
                    "message": f"invalid request containing {leaked_secret}",
                    "type": "invalid_request_error",
                }
            },
        )

    client = openai_client(handler)
    provider = OpenAIModelProvider(
        client=client,
        max_retries=2,
        retry_backoff_seconds=0,
    )
    try:
        with pytest.raises(ModelProviderError) as error:
            await provider.complete(ModelRequest(messages=basic_messages()))
    finally:
        await client.close()

    assert attempts == 1
    assert str(error.value) == "OpenAI model request failed (HTTP 400)"
    assert leaked_secret not in str(error.value)


@pytest.mark.asyncio
async def test_openai_provider_failure_does_not_leak_into_runtime_events() -> None:
    provider_secret = "provider-error-secret"
    task_secret = "user-request-secret"

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            500,
            json={
                "error": {
                    "message": f"upstream included {provider_secret}",
                    "type": "server_error",
                }
            },
        )

    client = openai_client(handler)
    provider = OpenAIModelProvider(
        client=client,
        max_retries=0,
    )
    events = InMemoryEventSink()
    spec = AgentSpec(
        id="safe-agent",
        name="Safe Agent",
        version="1.0.0",
        instructions="Answer safely.",
        memory_namespace="safe-agent",
    )
    try:
        result = await AgentRuntime(
            model=provider,
            tools=ToolRegistry(),
            events=events,
        ).run(
            agent=spec,
            task=task_secret,
            context=RunContext(tenant_id="tenant-a"),
        )
    finally:
        await client.close()

    serialized = json.dumps(
        {
            "result_error": result.error,
            "events": [event.model_dump(mode="json") for event in events.events],
        },
        ensure_ascii=False,
    )
    assert result.status is RunStatus.FAILED
    assert provider_secret not in serialized
    assert "upstream included" not in serialized


@pytest.mark.asyncio
async def test_openai_provider_sanitizes_timeout_errors() -> None:
    leaked_secret = "upstream-secret-detail"

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout(f"timed out with {leaked_secret}")

    client = openai_client(handler)
    provider = OpenAIModelProvider(client=client, max_retries=0)
    try:
        with pytest.raises(ModelProviderError) as error:
            await provider.complete(ModelRequest(messages=basic_messages()))
    finally:
        await client.close()

    assert str(error.value) == "OpenAI model request timed out"
    assert leaked_secret not in str(error.value)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"api_key": "   "}, "must not be blank"),
        ({"api_key": "test", "model": "  "}, "must not be blank"),
        ({"api_key": "test", "max_output_tokens": 0}, "between 1 and 128000"),
        ({"api_key": "test", "timeout_seconds": 0}, "must be positive"),
        ({"api_key": "test", "max_retries": 6}, "between 0 and 5"),
        ({"api_key": "test", "retry_backoff_seconds": 6}, "between 0 and 5"),
    ],
)
def test_openai_provider_rejects_invalid_configuration(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OpenAIModelProvider(**kwargs)
