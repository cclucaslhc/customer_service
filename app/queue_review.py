"""Read-only operator inventory of terminal queued work; never replays work."""

import sqlalchemy as sa

from app.persistence.schema import metadata


_CHAT_UNKNOWN = {'lease_expired', 'worker_error', 'worker_exit', 'coordination_unavailable'}
_CHAT_NO_EXECUTION = {'queue_timeout', 'identity_revoked', 'session_inactive'}
_KNOWLEDGE_UNKNOWN = {'lease_expired', 'worker_failed'}
_KNOWLEDGE_NO_EXECUTION = {'queue_timeout', 'identity_revoked'}


def _role_is_readonly(db):
    return db.exec_driver_sql("""
        SELECT NOT (
            r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
            OR r.rolbypassrls OR r.rolinherit OR d.datdba = r.oid
            OR pg_has_role(r.oid, d.datdba, 'MEMBER')
            OR EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.member=r.oid)
            OR has_database_privilege(current_user, current_database(), 'CREATE')
            OR has_schema_privilege(current_user, 'public', 'CREATE')
            OR EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND (
                    (c.relkind IN ('r','p','v','m') AND (
                        c.relowner=r.oid OR has_table_privilege(current_user,c.oid,
                            'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER')
                    ))
                    OR (c.relkind='S' AND (
                        c.relowner=r.oid OR has_sequence_privilege(current_user,c.oid,'USAGE, UPDATE')
                    ))
                )
            )
        )
        AND has_database_privilege(current_user, current_database(), 'CONNECT')
        AND has_schema_privilege(current_user, 'public', 'USAGE')
        AND has_table_privilege(current_user, 'public.agent_runs', 'SELECT')
        AND has_table_privilege(current_user, 'public.knowledge_jobs', 'SELECT')
        AND NOT EXISTS (
            SELECT 1 FROM pg_class
            WHERE oid IN ('public.agent_runs'::regclass,'public.knowledge_jobs'::regclass)
              AND relrowsecurity
        )
        FROM pg_roles r JOIN pg_database d ON d.datname=current_database()
        WHERE r.rolname=current_user
    """).scalar_one_or_none() is True


def _chat_disposition(code):
    if code in _CHAT_UNKNOWN:
        return code, 'review_unknown'
    if code in _CHAT_NO_EXECUTION:
        return code, 'terminal_no_execution'
    return 'other', 'review_unknown'


def _knowledge_disposition(stage):
    if stage in _KNOWLEDGE_UNKNOWN:
        return stage, 'review_unknown'
    if stage in _KNOWLEDGE_NO_EXECUTION:
        return stage, 'terminal_no_execution'
    return 'item_failure', 'review_items'


def list_queue_failures(engine, *, limit=100):
    """Return bounded, fixed-field rows with a read-only PostgreSQL identity.

    The report intentionally contains no prompt, message, path, error text,
    provider, credential, or per-item result. It never changes queue state.
    """
    if engine.dialect.name != 'postgresql':
        raise ValueError('Queue review requires PostgreSQL')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Queue review limit must be between 1 and 200')
    runs,jobs = metadata.tables['agent_runs'],metadata.tables['knowledge_jobs']
    with engine.begin() as db:
        db.exec_driver_sql('SET TRANSACTION READ ONLY')
        db.exec_driver_sql("SET LOCAL statement_timeout='5s'")
        if not _role_is_readonly(db):
            raise PermissionError('Queue review requires a dedicated read-only PostgreSQL role')
        chat = db.execute(sa.select(runs.c.id,runs.c.tenant_id,runs.c.status,runs.c.error_code,
                runs.c.finished_at).where(runs.c.execution_mode == 'queued',
                runs.c.status.in_(('error','interrupted')),
                sa.or_(runs.c.error_code.is_(None),runs.c.error_code.notin_(('cancelled','superseded'))))
            .order_by(runs.c.finished_at.desc(),runs.c.id.desc()).limit(limit)).mappings().all()
        knowledge = db.execute(sa.select(jobs.c.id,jobs.c.tenant_id,jobs.c.status,jobs.c.stage,
                jobs.c.finished_at,jobs.c.processed_count).where(jobs.c.execution_mode == 'queued',
                jobs.c.status.in_(('failed','partial')))
            .order_by(jobs.c.finished_at.desc(),jobs.c.id.desc()).limit(limit)).mappings().all()
    result = []
    for row in chat:
        reason,disposition = _chat_disposition(row['error_code'])
        result.append({'task_type':'chat','id':str(row['id']),'tenant_id':str(row['tenant_id']),
            'status':row['status'],'reason':reason,'disposition':disposition,
            'finished_at':row['finished_at'].isoformat() if row['finished_at'] else None})
    for row in knowledge:
        reason,disposition = _knowledge_disposition(row['stage'])
        result.append({'task_type':'knowledge','id':str(row['id']),'tenant_id':str(row['tenant_id']),
            'status':row['status'],'reason':reason,'disposition':disposition,
            'processed_count':row['processed_count'],
            'finished_at':row['finished_at'].isoformat() if row['finished_at'] else None})
    result.sort(key=lambda row:(row['finished_at'] or '',row['id']),reverse=True)
    return result[:limit]
