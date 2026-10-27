from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent import AgentDeps, agent, unsupported_fact_anchors, validate_final_answer


def test_empty_knowledge_results_cannot_be_overridden_by_model_claim():
    deps = AgentDeps(None, knowledge_searched=True)
    answer = validate_final_answer(SimpleNamespace(deps=deps), "公司保修期为 99 年。")
    assert "99" not in answer
    assert "无法确认" in answer
    assert deps.validation_error is None


def test_knowledge_gate_does_not_change_unsearched_answer():
    deps = AgentDeps(None)
    assert validate_final_answer(SimpleNamespace(deps=deps), "现在是工作时间。") == "现在是工作时间。"
    assert deps.validation_error is None


@pytest.mark.asyncio
async def test_actual_agent_replaces_unsupported_knowledge_claim():
    class EmptyKnowledge:
        def search(self, query):
            assert query == "保修期"
            return []

    def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            return ModelResponse(parts=[TextPart("公司保修期为 99 年。")])
        return ModelResponse(parts=[ToolCallPart("search_knowledge", {"query": "保修期"})])

    deps = AgentDeps(EmptyKnowledge())
    result = await agent.run("公司保修期多长？", model=FunctionModel(model), deps=deps)
    assert "99" not in result.output
    assert "无法确认" in result.output
    assert deps.citations == []


def test_fact_anchor_gate_rejects_unseen_number_and_model():
    assert unsupported_fact_anchors(
        "QX-900 保修 99 年。", ["QX-800 保修 7 年。"]
    ) == {"qx-900", "99"}
    deps = AgentDeps(None, knowledge_searched=True,
                     citations=[{"document_id": "synthetic"}],
                     citation_sources=[{"id": 1}],
                     knowledge_evidence=["QX-800 保修 7 年。"])
    answer = validate_final_answer(SimpleNamespace(deps=deps), "QX-900 保修 99 年。")
    assert "无法确认" in answer
    assert deps.citations == deps.citation_sources == []


def test_fact_anchor_gate_accepts_supported_anchor_and_reference_label():
    deps = AgentDeps(None, knowledge_searched=True,
                     citations=[{"document_id": "synthetic"}],
                     knowledge_evidence=["QX-900 保修 7 年。"])
    answer = "QX-900 保修 7 年。[资料 1]"
    assert validate_final_answer(SimpleNamespace(deps=deps), answer) == answer


def test_fact_anchor_gate_does_not_join_unrelated_chunks():
    sources = ["QX-900 保修 2 年。", "QX-800 保修 7 年。"]
    assert unsupported_fact_anchors("QX-900 保修 7 年。", sources) == {"qx-900", "7"}
    assert unsupported_fact_anchors("QX-900 保修 2 年。QX-800 保修 7 年。", sources) == set()
    assert unsupported_fact_anchors("QX-900 保修 7 年。", ["QX-900 保修 2 年。QX-800 保修 7 年。"])


def test_fact_anchor_gate_checks_comma_boundaries_and_explicit_source_numbers():
    sources = ["QX-900 保修 2 年，QX-800 保修 7 年。", "QX-900 保修 3 年。"]
    assert unsupported_fact_anchors("QX-900 保修 7 年。", sources) == {"qx-900", "7"}
    assert unsupported_fact_anchors("QX-900 保修 3 年[资料 1]。", sources) == {"qx-900", "3"}
    assert unsupported_fact_anchors("QX-900 保修 3 年[资料 2]。", sources) == set()
    assert unsupported_fact_anchors("见[资料 3]。", sources) == {"invalid_source_reference"}


@pytest.mark.asyncio
async def test_actual_agent_rejects_cross_source_number_assignment():
    class ConflictingKnowledge:
        def search(self, query):
            assert query == "QX-900 保修"
            return [
                {"document_id": "a", "display_name": "产品A", "section": "保修", "location": "A",
                 "content": "QX-900 保修 2 年。"},
                {"document_id": "b", "display_name": "产品B", "section": "保修", "location": "B",
                 "content": "QX-800 保修 7 年。"},
            ]

    def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            return ModelResponse(parts=[TextPart("QX-900 保修 7 年。")])
        return ModelResponse(parts=[ToolCallPart("search_knowledge", {"query": "QX-900 保修"})])

    deps = AgentDeps(ConflictingKnowledge())
    result = await agent.run("QX-900 保修多久？", model=FunctionModel(model), deps=deps)
    assert "无法确认" in result.output
    assert deps.citations == []
