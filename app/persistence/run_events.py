"""Internal ordered event writes; caller holds the Run row/session transaction lock."""
import json
import uuid
import sqlalchemy as sa
from app.persistence.schema import metadata


def append(db, run, event, data, *, terminal_event=False):
    table, runs = metadata.tables['chat_run_events'], metadata.tables['agent_runs']
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > 262144:
        raise ValueError('Run event exceeds size limit')
    # Lifetime counters survive pruning; no full ledger scan on every token.
    size = db.execute(sa.select(sa.func.octet_length(sa.cast(sa.literal(data,type_=table.c.data.type),sa.Text())))).scalar_one()
    sequence = db.execute(runs.update().where(runs.c.id == run['id'],
        runs.c.event_data_bytes+size <= (4 if terminal_event else 3)*1024*1024,
        runs.c.event_sequence < (10000 if terminal_event else 9000))
        .values(event_sequence=runs.c.event_sequence+1,event_data_bytes=runs.c.event_data_bytes+size)
        .returning(runs.c.event_sequence)).scalar_one_or_none()
    if sequence is None:
        raise ValueError('Run event budget exceeded')
    db.execute(table.insert().values(tenant_id=run['tenant_id'],run_id=run['id'],sequence=sequence,
        event=event,data=data,created_at=sa.func.clock_timestamp()))
    return sequence


def terminal(db, run, status, code=None, *, message_id=None, citations=(), contact_summary=None):
    if run.get('execution_mode') != 'queued':
        return
    rid = str(run['id'])
    if status == 'complete':
        messages = metadata.tables['messages']
        message = db.execute(sa.select(messages.c.content,messages.c.citations_json).where(
            messages.c.id == uuid.UUID(str(message_id)),messages.c.tenant_id == run['tenant_id'],
            messages.c.session_id == run['session_id'],messages.c.role == 'assistant')).mappings().one()
        # Buffered knowledge answers are yielded only AFTER complete_run has
        # committed, so the Worker must not append those frames to a terminal
        # Run. Persist the authoritative full answer HERE, in the same atomic
        # transaction as the assistant row and done event. Reset earlier drafts
        # to avoid duplication for ordinary pre-commit streaming answers.
        append(db,run,'answer_reset',{},terminal_event=True)
        for offset in range(0,len(message['content']),8192):
            append(db,run,'token',{'text':message['content'][offset:offset+8192]},terminal_event=True)
        for citation in citations:
            append(db,run,'citation',citation,terminal_event=True)
        if contact_summary is not None:
            append(db,run,'contact_form',{'summary':contact_summary},terminal_event=True)
        append(db,run,'done',{'run_id':rid,'message_id':message_id},terminal_event=True)
        state = {'draft':message['content'],'citations':message['citations_json'],
            'message_id':message_id,'contact_summary':contact_summary}
    else:
        append(db,run,'answer_reset',{},terminal_event=True)
        if status == 'error' or code == 'identity_revoked':
            append(db,run,'error',{'code':'run_identity_revoked' if code == 'identity_revoked' else code or 'run_failed',
                'message':'运行未完成，请重新登录或稍后重试','run_id':rid},terminal_event=True)
        else:
            append(db,run,'interrupted',{'run_id':rid,'reason':code},terminal_event=True)
        state = {'draft':'','citations':[],'message_id':None,'contact_summary':None}
    snapshots,runs = metadata.tables['chat_run_snapshots'],metadata.tables['agent_runs']
    sequence = db.execute(sa.select(runs.c.event_sequence).where(runs.c.id == run['id'])).scalar_one()
    db.execute(snapshots.insert().values(tenant_id=run['tenant_id'],run_id=run['id'],sequence=sequence,state=state,
        created_at=sa.func.clock_timestamp()))
