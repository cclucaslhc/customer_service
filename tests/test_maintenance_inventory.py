"""No newly added business table may silently escape explicit sealing locks."""
from app.persistence.maintenance_current import PROTECTED_TABLES
from app.persistence.schema import metadata


def test_current_sealing_inventory_covers_all_non_control_tables_once():
    # These four control tables must remain available to the sealing/restore
    # protocol itself; exceptions are explicit, never inferred from names.
    controls={'maintenance_state','recovery_state','backup_schedules','backup_jobs'}
    assert set(PROTECTED_TABLES)==set(metadata.tables)-controls
    assert len(PROTECTED_TABLES)==len(set(PROTECTED_TABLES))
