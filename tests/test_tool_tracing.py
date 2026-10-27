"""Actual PydanticAI tool execution with synthetic model/data, no network or keys."""
import asyncio
import inspect
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.agent import agent, AgentDeps, search_knowledge, get_business_time
from app.identity import AuthenticationError
from app.tracing import SafeTracing, agent_scope, capture_carrier, traced_tool


class SyntheticKnowledge:
    def search(self, query):
        assert query == 'SECRET_QUERY'
        return [{'document_id': 'SECRET_DOC', 'display_name': 'SECRET_NAME',
                 'section': 'SECRET_SECTION', 'location': 'SECRET_LOCATION',
                 'content': 'SECRET_CONTENT'}]


def span_evidence(spans):
    return json.dumps([{'name': span.name, 'attributes': dict(span.attributes),
                       'status': span.status.description} for span in spans])


@pytest.mark.asyncio
async def test_actual_pydantic_tools_parallel_runs_and_rag_chain_are_content_free():
    def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for msg in messages for part in msg.parts):
            return ModelResponse(parts=[TextPart('已完成合成客服工具验收。')])
        return ModelResponse(parts=[
            ToolCallPart('search_knowledge', {'query': 'SECRET_QUERY'}),
            ToolCallPart('get_business_time', {}),
            ToolCallPart('lookup_order', {'order_id': 'SECRET_ORDER', 'phone_suffix': 'SECRET_PHONE'}),
            ToolCallPart('lookup_shipping', {'order_id': 'SECRET_ORDER'}),
            ToolCallPart('check_refund_eligibility', {'order_id': 'SECRET_ORDER'}),
            ToolCallPart('show_contact_form', {'reason': 'SECRET_REASON', 'summary': 'SECRET_SUMMARY'}),
        ])
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    try:
        async def run():
            request_id, run_id = str(uuid4()), str(uuid4())
            with tracing.scope('http.request', root=True, request_id=request_id):
                async with agent_scope(run_id):
                    result = await agent.run('SECRET_PROMPT', model=FunctionModel(model),
                                             deps=AgentDeps(SyntheticKnowledge()))
                    assert result.output == '已完成合成客服工具验收。'
            assert capture_carrier() is None
            return request_id, run_id
        runs = await asyncio.gather(*(run() for _ in range(4)))
        assert tracing.flush(2)
        spans = exporter.get_finished_spans()
        assert len(spans) == 36
        for request_id, run_id in runs:
            group = [s for s in spans if s.attributes['request_id'] == request_id]
            root = next(s for s in group if s.name == 'http.request')
            agent_span = next(s for s in group if s.name == 'agent.run')
            tools = [s for s in group if s.name == 'tool.execute']
            rag = next(s for s in group if s.name == 'rag.search')
            assert agent_span.parent.span_id == root.context.span_id
            assert len(tools) == 6
            assert len({s.attributes['attempt_id'] for s in tools}) == 6
            assert all(s.parent.span_id == agent_span.context.span_id for s in tools)
            assert all(s.attributes['run_id'] == run_id for s in tools + [rag])
            search = next(s for s in tools if s.attributes['tool.name'] == 'search_knowledge')
            assert rag.parent.span_id == search.context.span_id
            assert rag.attributes['attempt_id'] == search.attributes['attempt_id']
            assert rag.attributes['rag.stage'] == 'retrieve'
            assert all(s.attributes['outcome'] == 'completed' for s in tools + [rag])
            assert len({s.context.trace_id for s in group}) == 1
        assert 'SECRET_' not in span_evidence(spans)
        assert all(not s.events and not s.links for s in spans)
        assert tracing.snapshot()['rejected'] == 0
    finally:
        tracing.close()
    assert not tracing.snapshot()['worker_alive']


def test_revoked_identity_creates_failed_tool_but_never_enters_retrieval():
    class ForbiddenKnowledge:
        def search(self, query):
            raise AssertionError('Knowledge must not be accessed')
    def revoked():
        raise AuthenticationError()
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    try:
        with tracing.scope('agent.run', root=True, request_id=str(uuid4()), run_id=str(uuid4())):
            with pytest.raises(AuthenticationError):
                search_knowledge(SimpleNamespace(deps=AgentDeps(
                    ForbiddenKnowledge(), identity_resolver=revoked)), 'SECRET_QUERY')
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        tool = next(s for s in spans if s.name == 'tool.execute')
        assert tool.attributes['outcome'] == 'error'
        assert tool.status.status_code.name == 'ERROR'
        assert not any(s.name == 'rag.search' for s in spans)
        assert 'SECRET_' not in span_evidence(spans)
    finally:
        tracing.close()


@pytest.mark.asyncio
async def test_async_tool_cancel_and_retrieval_failure_restore_context():
    @traced_tool('lookup_shipping')
    async def cancelled():
        raise asyncio.CancelledError('SECRET_CANCEL')
    class BrokenKnowledge:
        def search(self, query):
            raise RuntimeError('SECRET_RETRIEVAL_FAILURE')
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    try:
        with tracing.scope('agent.run', root=True, request_id=str(uuid4()), run_id=str(uuid4())):
            parent = capture_carrier()
            with pytest.raises(asyncio.CancelledError):
                await cancelled()
            assert capture_carrier() == parent
            with pytest.raises(RuntimeError):
                await asyncio.to_thread(search_knowledge,
                    SimpleNamespace(deps=AgentDeps(BrokenKnowledge())), 'SECRET_QUERY')
            assert capture_carrier() == parent
        assert capture_carrier() is None
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        outcomes = [s.attributes['outcome'] for s in spans if s.name == 'tool.execute']
        assert sorted(outcomes) == ['cancelled', 'error']
        assert next(s for s in spans if s.name == 'rag.search').attributes['outcome'] == 'error'
        assert 'SECRET_' not in span_evidence(spans)
    finally:
        tracing.close()


def test_disabled_instrumentation_keeps_signature_and_behavior():
    assert str(inspect.signature(search_knowledge)) == str(inspect.signature(search_knowledge.__wrapped__))
    assert inspect.signature(get_business_time).parameters == {}
    deps = AgentDeps(SyntheticKnowledge())
    result = search_knowledge(SimpleNamespace(deps=deps), 'SECRET_QUERY')
    assert 'SECRET_CONTENT' in result
    assert deps.citations and capture_carrier() is None
    assert 'weekday' in get_business_time()
    with pytest.raises(ValueError, match='Unregistered trace tool'):
        traced_tool('SECRET_TOOL')


def test_unknown_operation_labels_cannot_enter_exporter():
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    try:
        with tracing.scope('tool.execute', root=True, tool_name='search_knowledge') as span:
            span.set_attribute('tool.name', 'SECRET_TOOL')
        with tracing.scope('rag.search', root=True, rag_stage='retrieve') as span:
            span.set_attribute('rag.stage', 'SECRET_STAGE')
        with pytest.raises(ValueError, match='Invalid RAG trace stage'):
            with tracing.scope('rag.vector', root=True, rag_stage='rerank'):
                pass
        assert tracing.flush()
        assert not exporter.get_finished_spans()
        assert tracing.snapshot()['rejected'] == 2
    finally:
        tracing.close()
