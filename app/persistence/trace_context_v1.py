"""Immutable 0019 internal trace carrier: no content, baggage or arbitrary fields."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

CHECK = """trace_context IS NULL OR (
jsonb_typeof(trace_context) = 'object'
AND trace_context ?& ARRAY['request_id','trace_id','span_id','sampled']
AND (trace_context - ARRAY['request_id','trace_id','span_id','sampled']) = '{}'::jsonb
AND jsonb_typeof(trace_context->'request_id') = 'string'
AND jsonb_typeof(trace_context->'trace_id') = 'string'
AND jsonb_typeof(trace_context->'span_id') = 'string'
AND jsonb_typeof(trace_context->'sampled') = 'boolean'
AND (trace_context->>'request_id') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
AND (trace_context->>'trace_id') ~ '^[0-9a-f]{32}$'
AND (trace_context->>'trace_id') <> repeat('0',32)
AND (trace_context->>'span_id') ~ '^[0-9a-f]{16}$'
AND (trace_context->>'span_id') <> repeat('0',16))"""


def add_trace_context(metadata):
    table = metadata.tables['chat_run_queue']
    table.append_column(sa.Column('trace_context',JSONB(none_as_null=True),nullable=True))
    table.append_constraint(sa.CheckConstraint(CHECK,name='queue_trace_context'))


def add_knowledge_trace_context(metadata):
    table = metadata.tables['knowledge_job_queue']
    table.append_column(sa.Column('trace_context',JSONB(none_as_null=True),nullable=True))
    table.append_constraint(sa.CheckConstraint(CHECK,name='knowledge_queue_trace_context'))
