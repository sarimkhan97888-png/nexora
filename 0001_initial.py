"""initial schema (bootstraps from model metadata; use autogenerate for later changes)"""
from alembic import op

from nexora.db import models, models_core  # noqa: F401

revision = "0001"
down_revision = None


def upgrade() -> None:
    models.Base.metadata.create_all(op.get_bind())
    # Append-only audit trail (only if a separate least-privilege role exists):
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='nexora_app') THEN
        REVOKE UPDATE, DELETE ON audit_logs FROM nexora_app;
      END IF; END $$;""")


def downgrade() -> None:
    models.Base.metadata.drop_all(op.get_bind())
