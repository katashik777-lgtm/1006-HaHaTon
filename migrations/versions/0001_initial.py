"""Transactional metadata. Analytics are immutable Parquet artifacts."""
revision='0001'
down_revision=None
from alembic import op
from spr.db import Base

def upgrade():Base.metadata.create_all(op.get_bind())
def downgrade():Base.metadata.drop_all(op.get_bind())
