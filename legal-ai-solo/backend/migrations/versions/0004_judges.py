"""Panel and reporting judge of VKS acts (public in the acts), for the research by judge.

Revision ID: 0004
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS panel jsonb")
    op.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS reporter text")
    op.execute("CREATE INDEX IF NOT EXISTS decisions_reporter_idx ON decisions (reporter) WHERE reporter IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS decisions_reporter_idx")
    op.execute("ALTER TABLE decisions DROP COLUMN IF EXISTS reporter")
    op.execute("ALTER TABLE decisions DROP COLUMN IF EXISTS panel")
