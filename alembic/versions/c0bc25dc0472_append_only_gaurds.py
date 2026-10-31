"""append only gaurds

Revision ID: c0bc25dc0472
Revises: 4ab37ee14efb
Create Date: 2026-10-31 12:36:44.427297

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0bc25dc0472'
down_revision: Union[str, Sequence[str], None] = '4ab37ee14efb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION forbid_update_delete() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Append-only table: UPDATE and DELETE are not allowed';
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ("audit_events", "verifications"):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
        """)


def downgrade() -> None:
    for table in ("audit_events", "verifications"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
    op.execute("DROP FUNCTION IF EXISTS forbid_update_delete();")