"""Offline tests only; never load management credentials during normal pytest."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse,TextPart
from pydantic_ai.usage import RequestUsage
import pytest

ROOT=Path(__file__).resolve().parents[1]


def module():
    spec=importlib.util.spec_from_file_location('configured_probe',ROOT/'scripts/verify_configured_deepseek.py')
    item=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


def test_synthetic_probe_records_real_usage_property_without_secret_output():
    def respond(messages,info):
        assert not info.function_tools
        assert info.model_settings['max_tokens']==16
        return ModelResponse([TextPart('S2_OK')],usage=RequestUsage(input_tokens=20,output_tokens=3))
    class Gateway:
        def list_configs(self): return {'active_provider':'deepseek'}
        def snapshot(self): return SimpleNamespace(provider='deepseek',model='offline-test')
        def build_model(self,snapshot): return FunctionModel(respond)
    result=asyncio.run(module().probe(Gateway()))
    assert result['requests']==1 and result['input_tokens']==20 and result['output_tokens']==3
    assert not result['invoice_reconciled'] and not result['pg_budget_verified']
    assert 'api_key' not in result


def test_probe_rejects_non_deepseek_before_acquiring_credentials():
    class Gateway:
        def list_configs(self): return {'active_provider':'openai'}
        def snapshot(self): raise AssertionError('Must not acquire key')
    with pytest.raises(ValueError,match='DeepSeek'):
        asyncio.run(module().probe(Gateway()))
