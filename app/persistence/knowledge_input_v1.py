"""Immutable 0015 versioned input references; old local manifests stay local."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_knowledge_inputs(meta):
    table = meta.tables['knowledge_job_items']
    table.append_column(sa.Column('input_object', sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), 'postgresql'), nullable=True))
    table.append_constraint(sa.CheckConstraint(
        "input_object IS NULL OR (jsonb_typeof(input_object) = 'object' AND sha256 IS NOT NULL)",
        name='knowledge_input_object_shape'))
