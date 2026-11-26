"""fork root: the start of the Securo Custom migration line, no schema change

Revision ID: f000
Revises: 131

Fork migrations live in this directory on the `fork` branch label, so upstream's
numbered line in `versions/` never collides with ours (docs/fork/DECISIONS.md,
D-003). Feature migrations chain f001, f002, ... after this one. Deploys run
`alembic upgrade heads`; `alembic downgrade fork@f000` removes every fork
migration above this root and leaves upstream's line alone.
"""

revision = "f000"
down_revision = "131"
branch_labels = ("fork",)
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
