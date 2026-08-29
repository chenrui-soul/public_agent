from pathlib import Path

import pytest

from public_agent.core.types import ModelResponse
from public_agent.factory import AgentFactory
from public_agent.providers.testing import ScriptedModelProvider
from public_agent.tools.registry import ToolRegistry


def calculator_domain() -> Path:
    return Path(__file__).parents[1] / "examples" / "domain_packs" / "calculator"


def test_factory_rejects_missing_domain_tools() -> None:
    with pytest.raises(ValueError, match="unregistered tools: add_numbers"):
        AgentFactory().create(
            domain_path=calculator_domain(),
            model=ScriptedModelProvider([ModelResponse(content="unused")]),
            tools=ToolRegistry(),
        )
