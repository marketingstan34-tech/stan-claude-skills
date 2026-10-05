"""Public corpus: source artifacts, decisions, immutable versions, passages, snapshots.

Phase A-B only. Case/document/fact tables belong to Phase C and are not created here.

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE source_artifacts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source text NOT NULL,
        url text NOT NULL,
        retrieved_at timestamptz,
        acquisition text NOT NULL CHECK (acquisition IN ('direct', 'firecrawl', 'manual')),
        mime text NOT NULL DEFAULT 'text/html',
        sha256 char(64) NOT NULL,
        storage_key text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source, sha256)
    );

    CREATE TABLE source_list_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source text NOT NULL,
        url text NOT NULL,
        description text NOT NULL,
        row_count integer NOT NULL,
        truncated boolean NOT NULL,
        artifact_id uuid REFERENCES source_artifacts(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source, url, artifact_id)
    );

    CREATE TABLE decisions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source text NOT NULL,
        source_record_id text NOT NULL,
        court text NOT NULL,
        chamber text,
        act_type text,
        act_number text,
        act_date date,
        case_type text,
        case_number text,
        case_year integer,
        proceeding_article text,
        admission_grounds jsonb NOT NULL DEFAULT '[]',
        canonical_url text NOT NULL,
        current_version_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source, source_record_id)
    );
    CREATE INDEX decisions_date_idx ON decisions (act_date);
    CREATE INDEX decisions_case_idx ON decisions (case_number, case_year);

    CREATE TABLE decision_versions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        decision_id uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
        artifact_id uuid NOT NULL REFERENCES source_artifacts(id),
        canonical_text text NOT NULL,
        text_hash char(64) NOT NULL,
        parser_version text NOT NULL,
        warnings jsonb NOT NULL DEFAULT '[]',
        parsed_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (decision_id, text_hash, parser_version)
    );
    ALTER TABLE decisions ADD CONSTRAINT decisions_current_version_fk
        FOREIGN KEY (current_version_id) REFERENCES decision_versions(id);

    CREATE TABLE passages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        decision_version_id uuid NOT NULL REFERENCES decision_versions(id) ON DELETE CASCADE,
        paragraph_no integer NOT NULL,
        section text NOT NULL CHECK (section IN ('reasoning', 'dispositive')),
        start_offset integer NOT NULL,
        end_offset integer NOT NULL,
        exact_text text NOT NULL,
        is_admission boolean NOT NULL DEFAULT false,
        search_text text NOT NULL,
        tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
        UNIQUE (decision_version_id, paragraph_no),
        CHECK (start_offset >= 0 AND end_offset > start_offset)
    );
    CREATE INDEX passages_tsv_idx ON passages USING gin (tsv);

    CREATE TABLE corpus_snapshots (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        scope text NOT NULL,
        manifest jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE corpus_members (
        corpus_snapshot_id uuid NOT NULL REFERENCES corpus_snapshots(id) ON DELETE CASCADE,
        decision_version_id uuid NOT NULL REFERENCES decision_versions(id),
        PRIMARY KEY (corpus_snapshot_id, decision_version_id)
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE corpus_members, corpus_snapshots, passages;
    ALTER TABLE decisions DROP CONSTRAINT decisions_current_version_fk;
    DROP TABLE decision_versions, decisions, source_list_runs, source_artifacts;
    """)
