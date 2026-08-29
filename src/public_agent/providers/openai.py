from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from typing import Any, cast

from openai import APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError
from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseInputItemParam,
)
from pydantic import SecretStr

from public_agent.core.types import (
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)

_OPENAI_STATE_KEY = "openai"
_OPENAI_OUTPUT_KEY = "output"


class ModelProviderError(RuntimeError):
    """Safe model provider failure without raw request or response details."""


class OpenAIModelProvider:
    """Production OpenAI Responses API adapter with strict tool boundaries."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None = None,
        model: str = "gpt-5.6-terra",
        max_output_tokens: int = 4096,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("OpenAI model must not be blank")
        if not 1 <= max_output_tokens <= 128000:
            raise ValueError("OpenAI max output tokens must be between 1 and 128000")
        if timeout_seconds <= 0:
            raise ValueError("OpenAI model timeout must be positive")
        if not 0 <= max_retries <= 5:
            raise ValueError("OpenAI model retries must be between 0 and 5")
        if not 0 <= retry_backoff_seconds <= 5:
            raise ValueError("OpenAI retry backoff must be between 0 and 5 seconds")
        if client is None and api_key is None:
            raise ValueError("OpenAI API key is required when no client is supplied")

        self._model = normalized_model
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._owns_client = client is None

        if client is not None:
            self._client = client.with_options(
                max_retries=0,
                timeout=timeout_seconds,
            )
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
                max_retries=0,
            )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        allowed_tools = {tool.name for tool in request.tools}
        if len(allowed_tools) != len(request.tools):
            raise ModelProviderError("OpenAI request contains duplicate tool names")
        input_items = _response_input(
            request.messages,
            allowed_tools=allowed_tools,
        )
        tools = tuple(_function_tool(tool) for tool in request.tools)
        request_fingerprint = _request_fingerprint(
            model=self._model,
            input_items=input_items,
            tools=tools,
            max_output_tokens=self._max_output_tokens,
        )

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.responses.create(
                    model=self._model,
                    input=input_items,
                    tools=tools,
                    max_output_tokens=self._max_output_tokens,
                    parallel_tool_calls=True,
                    store=False,
                    truncation="disabled",
                    extra_headers={
                        "Idempotency-Key": f"public-agent-{request_fingerprint}"
                    },
                    timeout=self._timeout_seconds,
                )
            except OpenAIError as exc:
                if attempt < self._max_retries and _is_retryable(exc):
                    await asyncio.sleep(self._retry_backoff_seconds * (2**attempt))
                    continue
                raise ModelProviderError(_safe_request_error(exc)) from None
            except (TypeError, ValueError):
                raise ModelProviderError(
                    "OpenAI model response could not be decoded"
                ) from None
            return _model_response(response, request.tools)

        raise AssertionError("unreachable OpenAI retry loop")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()


def _response_input(
    messages: tuple[Message, ...],
    *,
    allowed_tools: set[str],
) -> list[ResponseInputItemParam]:
    if not messages:
        raise ModelProviderError("OpenAI model request requires at least one message")

    input_items: list[ResponseInputItemParam] = []
    pending_calls: dict[str, str] = {}
    seen_call_ids: set[str] = set()

    for message in messages:
        if pending_calls and message.role is not MessageRole.TOOL:
            raise ModelProviderError(
                "OpenAI tool call history contains a missing tool output"
            )

        if message.role is MessageRole.TOOL:
            _append_tool_output(input_items, message, pending_calls)
            continue

        if message.role is MessageRole.ASSISTANT:
            call_items = _append_assistant_message(
                input_items,
                message,
                allowed_tools=allowed_tools,
            )
            for call in call_items:
                if call.id in seen_call_ids:
                    raise ModelProviderError(
                        "OpenAI tool call history contains a duplicate call id"
                    )
                seen_call_ids.add(call.id)
                pending_calls[call.id] = call.name
            continue

        if message.tool_calls or message.tool_call_id or message.name:
            raise ModelProviderError(
                "OpenAI non-assistant message contains tool call fields"
            )
        if message.provider_state:
            raise ModelProviderError(
                "OpenAI non-assistant message contains provider state"
            )
        if not message.content.strip():
            raise ModelProviderError("OpenAI input message content must not be blank")
        input_items.append(
            cast(
                ResponseInputItemParam,
                {
                    "type": "message",
                    "role": message.role.value,
                    "content": message.content,
                },
            )
        )

    if pending_calls:
        raise ModelProviderError("OpenAI tool call history contains a missing tool output")
    return input_items


def _append_assistant_message(
    input_items: list[ResponseInputItemParam],
    message: Message,
    *,
    allowed_tools: set[str],
) -> tuple[ToolCall, ...]:
    if message.tool_call_id or message.name:
        raise ModelProviderError("OpenAI assistant message contains tool output fields")

    stored_output = _stored_openai_output(
        message,
        allowed_tools=allowed_tools,
    )
    if stored_output is not None:
        output_items, stored_content, stored_calls = stored_output
        if stored_content != message.content or stored_calls != message.tool_calls:
            raise ModelProviderError("OpenAI provider state is inconsistent")
        input_items.extend(
            cast(ResponseInputItemParam, copy.deepcopy(item)) for item in output_items
        )
        return stored_calls

    if message.content:
        input_items.append(
            cast(
                ResponseInputItemParam,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": message.content,
                },
            )
        )
    for call in message.tool_calls:
        _validate_tool_call_identity(call.id, call.name)
        if call.name not in allowed_tools:
            raise ModelProviderError("OpenAI tool call history contains an unknown tool")
        input_items.append(
            cast(
                ResponseInputItemParam,
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": _canonical_json(call.arguments),
                },
            )
        )
    if not message.content and not message.tool_calls:
        raise ModelProviderError("OpenAI assistant message has no usable content")
    return message.tool_calls


def _append_tool_output(
    input_items: list[ResponseInputItemParam],
    message: Message,
    pending_calls: dict[str, str],
) -> None:
    if message.tool_calls or message.provider_state:
        raise ModelProviderError("OpenAI tool output contains assistant-only fields")
    call_id = (message.tool_call_id or "").strip()
    name = (message.name or "").strip()
    if not call_id or not name:
        raise ModelProviderError("OpenAI tool output requires call id and name")
    expected_name = pending_calls.get(call_id)
    if expected_name is None:
        raise ModelProviderError("OpenAI tool output is out of order or duplicated")
    if expected_name != name:
        raise ModelProviderError("OpenAI tool output name does not match its call")
    input_items.append(
        cast(
            ResponseInputItemParam,
            {
                "type": "function_call_output",
                "call_id": call_id,
                "name": name,
                "output": message.content,
            },
        )
    )
    del pending_calls[call_id]


def _stored_openai_output(
    message: Message,
    *,
    allowed_tools: set[str],
) -> tuple[list[dict[str, Any]], str, tuple[ToolCall, ...]] | None:
    state = message.provider_state.get(_OPENAI_STATE_KEY)
    if state is None:
        return None
    if not isinstance(state, dict):
        raise ModelProviderError("OpenAI provider state is invalid")
    raw_output = state.get(_OPENAI_OUTPUT_KEY)
    if not isinstance(raw_output, list) or not raw_output:
        raise ModelProviderError("OpenAI provider state has no output items")
    if not all(isinstance(item, dict) for item in raw_output):
        raise ModelProviderError("OpenAI provider state contains an invalid output item")
    output_items = cast(list[dict[str, Any]], raw_output)
    content, calls = _validated_serialized_output(
        output_items,
        allowed_tools=allowed_tools,
    )
    return output_items, content or "", calls


def _function_tool(tool: ToolDefinition) -> FunctionToolParam:
    _validate_tool_call_identity("definition", tool.name)
    definition: FunctionToolParam = {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": copy.deepcopy(tool.input_schema),
        "strict": True,
    }
    if tool.output_schema is not None:
        definition["output_schema"] = copy.deepcopy(tool.output_schema)
    return definition


def _model_response(
    response: Response,
    definitions: tuple[ToolDefinition, ...],
) -> ModelResponse:
    if response.error is not None or response.status != "completed":
        raise ModelProviderError("OpenAI model response was not completed")

    serialized_output = [
        item.model_dump(mode="json", exclude_none=True) for item in response.output
    ]
    allowed_tools = {definition.name for definition in definitions}
    content, calls = _validated_serialized_output(
        serialized_output,
        allowed_tools=allowed_tools,
    )
    if content is None and not calls:
        raise ModelProviderError("OpenAI model returned no usable output")

    return ModelResponse(
        content=content,
        tool_calls=calls,
        model_name=str(response.model),
        usage=_usage(response),
        provider_state={
            _OPENAI_STATE_KEY: {
                "response_id": response.id,
                _OPENAI_OUTPUT_KEY: serialized_output,
            }
        },
    )


def _validated_serialized_output(
    output_items: list[dict[str, Any]],
    *,
    allowed_tools: set[str] | None,
) -> tuple[str | None, tuple[ToolCall, ...]]:
    texts: list[str] = []
    calls: list[ToolCall] = []
    seen_call_ids: set[str] = set()

    for item in output_items:
        item_type = item.get("type")
        if item_type == "reasoning":
            if not isinstance(item.get("id"), str) or not isinstance(
                item.get("summary"), list
            ):
                raise ModelProviderError("OpenAI reasoning output is invalid")
            continue
        if item_type == "message":
            content = item.get("content")
            if item.get("role") != "assistant" or not isinstance(content, list):
                raise ModelProviderError("OpenAI message output is invalid")
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    raise ModelProviderError("OpenAI model returned an unsupported message output")
                text = part.get("text")
                if not isinstance(text, str):
                    raise ModelProviderError("OpenAI text output is invalid")
                texts.append(text)
            continue
        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise ModelProviderError("OpenAI function call identity is invalid")
            _validate_tool_call_identity(call_id, name)
            if call_id in seen_call_ids:
                raise ModelProviderError("OpenAI model returned a duplicate tool call id")
            if allowed_tools is not None and name not in allowed_tools:
                raise ModelProviderError("OpenAI model requested an unknown tool")
            if not isinstance(arguments, str):
                raise ModelProviderError("OpenAI function call arguments are invalid")
            parsed_arguments = _json_object(arguments)
            seen_call_ids.add(call_id)
            calls.append(ToolCall(id=call_id, name=name, arguments=parsed_arguments))
            continue
        raise ModelProviderError("OpenAI model returned an unsupported output item")

    joined_text = "".join(texts)
    return (joined_text if joined_text.strip() else None), tuple(calls)


def _usage(response: Response) -> dict[str, int]:
    if response.usage is None:
        return {}
    usage = response.usage
    values = {
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.input_tokens_details.cached_tokens,
        "cache_write_tokens": usage.input_tokens_details.cache_write_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.output_tokens_details.reasoning_tokens,
        "total_tokens": usage.total_tokens,
    }
    if any(value < 0 for value in values.values()):
        raise ModelProviderError("OpenAI token usage is invalid")
    return values


def _request_fingerprint(
    *,
    model: str,
    input_items: list[ResponseInputItemParam],
    tools: tuple[FunctionToolParam, ...],
    max_output_tokens: int,
) -> str:
    canonical = _canonical_json(
        {
            "model": model,
            "input": input_items,
            "tools": tools,
            "max_output_tokens": max_output_tokens,
            "parallel_tool_calls": True,
            "store": False,
            "truncation": "disabled",
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ModelProviderError("OpenAI request contains non-JSON data") from None


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        raise ModelProviderError("OpenAI function call arguments are not valid JSON") from None
    if not isinstance(parsed, dict):
        raise ModelProviderError("OpenAI function call arguments must be a JSON object")
    return parsed


def _validate_tool_call_identity(call_id: str, name: str) -> None:
    if not call_id.strip() or not name.strip():
        raise ModelProviderError("OpenAI tool call identity must not be blank")


def _is_retryable(exc: OpenAIError) -> bool:
    if isinstance(exc, APITimeoutError):
        return True
    status_code = getattr(exc, "status_code", None)
    return status_code == 429 or (
        isinstance(status_code, int) and 500 <= status_code <= 599
    )


def _safe_request_error(exc: OpenAIError) -> str:
    if isinstance(exc, APITimeoutError):
        return "OpenAI model request timed out"
    status_code = (
        exc.status_code
        if isinstance(exc, APIStatusError)
        else getattr(exc, "status_code", None)
    )
    suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
    return f"OpenAI model request failed{suffix}"
