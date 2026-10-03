"""The ruling on admission to cassation (чл. 288 ГПК) for VKS determinations.

Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS admission_result text")
    op.execute("CREATE INDEX IF NOT EXISTS decisions_admission_idx ON decisions (admission_result) "
               "WHERE admission_result IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS decisions_admission_idx")
    op.execute("ALTER TABLE decisions DROP COLUMN IF EXISTS admission_result")
