"""Opt-in real DeepSeek smoke with the explicitly authorized management config.

Only synthetic text leaves this host. No environment API key discovery, company
knowledge retrieval, tool execution, fallback, raw errors or credential output.
This proves live generation/usage, not invoice reconciliation or PG budgeting.
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def probe(gateway):
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits
    configs=gateway.list_configs()
    if configs['active_provider']!='deepseek':
        raise ValueError('Active configured provider must be DeepSeek')
    snapshot=gateway.snapshot()
    if snapshot.provider!='deepseek': raise ValueError('DeepSeek configuration changed')
    model=gateway.build_model(snapshot)
    started=time.monotonic()
    result=await asyncio.wait_for(Agent(model,retries=0).run(
        'Synthetic integration check. Reply with exactly S2_OK. Do not use tools.',
        model_settings={'max_tokens':16,'temperature':0},
        usage_limits=UsageLimits(request_limit=1)),timeout=45)
    usage=result.usage
    if result.output.strip()!='S2_OK': raise ValueError('Unexpected synthetic response')
    return {'provider':'deepseek','model':snapshot.model,'synthetic_response_verified':True,
        'requests':usage.requests,'input_tokens':usage.input_tokens,'output_tokens':usage.output_tokens,
        'cache_read_tokens':usage.cache_read_tokens,'cache_write_tokens':usage.cache_write_tokens,
        'elapsed_seconds':round(time.monotonic()-started,3),
        'company_content_sent':False,'invoice_reconciled':False,'pg_budget_verified':False}


async def main(output):
    from app.providers import ModelGateway
    from app.security import SecretStore
    if not output.is_absolute() or output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('New absolute evidence file in an existing directory required')
    gateway=ModelGateway(SecretStore())
    try:
        result=await probe(gateway)
        with output.open('x',encoding='utf-8') as target:
            json.dump(result,target,indent=2)
        print(json.dumps(result),flush=True)
    finally:
        await gateway.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-configured-deepseek',action='store_true',required=True,
        help='Explicit human authorization to use the management-configured credential')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    try: asyncio.run(main(args.output))
    except Exception as error:
        # Do not serialize provider/SDK exceptions, request bodies or tracebacks.
        print('Configured DeepSeek probe failed: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1) from None
