from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from public_agent.core.types import (
    ModelResponse,
    RunContext,
    ToolCall,
    ToolDefinition,
)
from public_agent.factory import AgentFactory
from public_agent.providers.testing import ScriptedModelProvider
from public_agent.tools.base import FunctionTool, ToolContext
from public_agent.tools.registry import ToolRegistry


async def add_numbers(arguments: dict[str, Any], context: ToolContext) -> dict[str, float]:
    del context
    return {"result": float(arguments["a"]) + float(arguments["b"])}


async def main() -> None:
    package_path = Path(__file__).parent / "domain_packs" / "calculator"
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            ToolDefinition(
                name="add_numbers",
                description="Add two numbers.",
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
            ),
            add_numbers,
        )
    )
    model = ScriptedModelProvider(
        [
            ModelResponse(
                tool_calls=(ToolCall(id="call-1", name="add_numbers", arguments={"a": 2, "b": 3}),)
            ),
            ModelResponse(content="The result is 5."),
        ]
    )
    agent = AgentFactory().create(
        domain_path=package_path,
        model=model,
        tools=registry,
    )
    result = await agent.run(
        task="What is 2 + 3?",
        context=RunContext(tenant_id="example"),
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
