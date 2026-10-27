"""Database-level maintenance fact, not a model-controlled approval."""
import sqlalchemy as sa


class RecoveryHold(RuntimeError):
    def __init__(self):
        super().__init__('Restored database is held pending authorized reconciliation')


def require_operational(db):
    if db.dialect.name != 'postgresql':
        return
    from app.persistence.schema import metadata
    state = metadata.tables['recovery_state']
    if db.execute(sa.select(state.c.blocked).where(state.c.id == 1)).scalar_one_or_none():
        raise RecoveryHold()
    maintenance = metadata.tables['maintenance_state']
    if db.execute(sa.select(maintenance.c.blocked).where(maintenance.c.id == 1)).scalar_one_or_none():
        from app.maintenance import MaintenanceHold
        raise MaintenanceHold()


def check_operational(engine):
    with engine.connect() as db:
        require_operational(db)
