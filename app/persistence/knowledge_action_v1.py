"""Pinned upload display names and reparse targets in the durable manifest."""
import sqlalchemy as sa


def add_knowledge_actions(meta):
    items = meta.tables['knowledge_job_items']
    for column in (sa.Column('display_name',sa.Text()),
                   sa.Column('replace_document_id',sa.Uuid()),
                   sa.Column('replace_document_version',sa.Integer())):
        items.append_column(column)
    items.append_constraint(sa.CheckConstraint(
        '(display_name IS NULL OR length(display_name) BETWEEN 1 AND 255) AND '
        '((replace_document_id IS NULL AND replace_document_version IS NULL) OR '
        '(replace_document_id IS NOT NULL AND replace_document_version IS NOT NULL AND replace_document_version > 0))',
        name='knowledge_action_manifest'))
