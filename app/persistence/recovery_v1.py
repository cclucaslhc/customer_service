"""Immutable 0014 recovery hold; fresh databases remain operational."""
import sqlalchemy as sa


def add_recovery_state(meta):
    sa.Table('recovery_state', meta,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('blocked', sa.Boolean(), nullable=False),
        sa.Column('backup_id', sa.Uuid(), nullable=False),
        sa.Column('restored_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='recovery_singleton'))
