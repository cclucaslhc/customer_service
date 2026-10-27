"""Shared PostgreSQL circuit proofs use disposable databases and synthetic runs."""

from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.usage import RequestUsage

from app.model_circuit import ModelCircuitOpen
from app.model_observability import observe_model
from app.persistence.schema import metadata
from test_model_usage import prepare, queue_setup, postgres_database
from test_model_cost import publish
from test_model_budget import activate, request
from test_durable_chat import submit


def test_cross_instance_open_half_open_probe_and_stale_fencing(queue_setup):
    bundle, _, _, _, _, rid, _, chat, meter = prepare(queue_setup)
    first = meter.circuit
    second = chat.model_meter(rid, provider='deepseek', model='synthetic').circuit
    for _ in range(3):
        permit = first.acquire()
        first.report(permit, healthy=False, failure=True)
    with pytest.raises(ModelCircuitOpen):
        second.acquire()
    table = metadata.tables['model_circuit_state']
    with bundle.engine.begin() as db:
        row = db.execute(sa.select(table).where(table.c.model == 'synthetic')).mappings().one()
        assert row['state'] == 'open' and row['failure_count'] == 3 and row['open_count'] == 1
        db.execute(table.update().where(table.c.tenant_id == row['tenant_id'],
            table.c.provider == 'deepseek',table.c.model == 'synthetic').values(
                open_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    probe = second.acquire()
    assert probe.owner is not None
    with pytest.raises(ModelCircuitOpen):
        first.acquire()
    first.report(permit, healthy=True)  # Old generation cannot close the probe.
    with pytest.raises(ModelCircuitOpen):
        first.acquire()
    second.report(probe, healthy=True)
    assert first.acquire().owner is None
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(table.c.open_count).where(table.c.model == 'synthetic')).scalar_one() == 1


def test_failed_or_expired_probe_reopens_and_old_owner_cannot_close(queue_setup):
    bundle, _, _, _, _, rid, _, chat, meter = prepare(queue_setup)
    circuit = meter.circuit
    table = metadata.tables['model_circuit_state']
    for _ in range(3):
        permit = circuit.acquire()
        circuit.report(permit, healthy=False, failure=True)
    with bundle.engine.begin() as db:
        db.execute(table.update().where(table.c.model == 'synthetic').values(
            open_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    old = circuit.acquire()
    with bundle.engine.begin() as db:
        db.execute(table.update().where(table.c.model == 'synthetic').values(
            probe_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    replacement = chat.model_meter(rid,provider='deepseek',model='synthetic').circuit
    new = replacement.acquire()
    assert old.owner != new.owner and old.generation != new.generation
    circuit.report(old,healthy=True)
    with pytest.raises(ModelCircuitOpen):
        circuit.acquire()
    replacement.report(new,healthy=False,failure=True)
    with bundle.engine.connect() as db:
        row = db.execute(sa.select(table.c.state,table.c.failure_count,table.c.open_count).where(
            table.c.model == 'synthetic')).one()
    assert row == ('open',3,2)


def test_circuit_state_is_tenant_bound_and_not_a_provider_attempt(queue_setup):
    bundle, _, _, _, _, rid, _, _, meter = prepare(queue_setup)
    permit = meter.circuit.acquire()
    meter.circuit.report(permit,healthy=True)
    table = metadata.tables['model_circuit_state']
    with bundle.engine.connect() as db:
        row = db.execute(sa.select(table).where(table.c.model == 'synthetic')).mappings().one()
        run = db.execute(sa.select(metadata.tables['agent_runs'].c.tenant_id).where(
            metadata.tables['agent_runs'].c.id == UUID(rid))).scalar_one()
        assert row['tenant_id'] == run and row['state'] == 'closed'
        assert db.execute(sa.select(sa.func.count()).select_from(
            metadata.tables['model_usage_attempts']).where(
            metadata.tables['model_usage_attempts'].c.run_id == UUID(rid))).scalar_one() == 0


def test_one_tenant_circuit_does_not_open_another_tenant(queue_setup):
    bundle, root, _, _, _, _, _, _, meter = prepare(queue_setup)
    for _ in range(3):
        permit = meter.circuit.acquire()
        meter.circuit.report(permit,healthy=False,failure=True)
    tenant = bundle.identity.create_tenant('Synthetic independent circuit')
    bundle.identity.provision_user('synthetic.circuit.other','synthetic-password-only',tenant,
        role='customer')
    token = bundle.identity.login('synthetic.circuit.other','synthetic-password-only',tenant).token
    sid, rid = submit(root.authenticated(token))
    job = root.claim_next()
    assert job['run_id'] == rid
    other = root.authenticated(job['credential']).with_lease(job['lease']).model_meter(
        rid,provider='deepseek',model='synthetic')
    assert other.circuit.acquire().owner is None
    with pytest.raises(ModelCircuitOpen):
        meter.circuit.acquire()


@pytest.mark.asyncio
async def test_model_boundary_skips_paid_attempt_while_shared_circuit_open(queue_setup):
    bundle, _, _, tokens, _, rid, _, _, meter = prepare(queue_setup)
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    activate(bundle,tokens)
    outbound = []
    def failing(messages,info):
        outbound.append('called')
        raise ModelHTTPError(503,'synthetic')
    model = observe_model(FunctionModel(failing),provider='deepseek',meter=meter)
    for _ in range(3):
        with pytest.raises(ModelHTTPError):
            await model.request(*request())
    with pytest.raises(ModelCircuitOpen):
        await model.request(*request())
    assert len(outbound) == 3
    assert len(meter.list_attempts()) == 3
    table = metadata.tables['model_circuit_state']
    with bundle.engine.begin() as db:
        db.execute(table.update().where(table.c.model == 'synthetic').values(
            open_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    def healthy(messages,info):
        outbound.append('probe')
        return ModelResponse([TextPart('synthetic recovered')],
            usage=RequestUsage(input_tokens=2,output_tokens=1))
    probe = observe_model(FunctionModel(healthy),provider='deepseek',meter=meter)
    result = await probe.request(*request())
    assert result.parts[0].content == 'synthetic recovered'
    assert outbound[-1] == 'probe' and len(meter.list_attempts()) == 4
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(table.c.state).where(table.c.model == 'synthetic')).scalar_one() == 'closed'
