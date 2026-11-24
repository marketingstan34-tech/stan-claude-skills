"""Enable row level security on every table, with no policies.

On Supabase, tables in the public schema are reachable through the Data API with the
project's anon key. RLS without policies denies that path. The application connects as
the table owner, which bypasses RLS, so local and Supabase deployments behave the same.

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = (
    "source_artifacts", "source_list_runs", "decisions", "decision_versions",
    "passages", "corpus_snapshots", "corpus_members",
)


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated';
        END IF;
    END $$;
    """)


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
