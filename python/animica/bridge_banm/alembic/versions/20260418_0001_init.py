"""Initial BANM bridge schema."""

from __future__ import annotations

from alembic import op

from animica.bridge_banm.db import Base
from animica.bridge_banm import models  # noqa: F401

# revision identifiers, used by Alembic.
revision = "20260418_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)

