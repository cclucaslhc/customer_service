"""Keep the isolated evaluation harness compatible with the model boundary."""

import pytest
from pydantic_ai.models.function import FunctionModel

from scripts.evaluate import StubGateway, configure_isolated_storage, evaluate_agent


@pytest.mark.asyncio
async def test_scripted_agent_evaluation_uses_supported_synthetic_provider(tmp_path):
    configure_isolated_storage(tmp_path)
    assert StubGateway(FunctionModel(lambda messages, info: None)).snapshot().provider == "synthetic"
    result = await evaluate_agent()
    assert result["cases"] == 7
    assert result["workflow_conformance_rate"] == 1.0
    assert result["tool_execution_success_rate"] == 1.0
