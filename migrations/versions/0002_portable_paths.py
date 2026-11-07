"""Replace machine-specific references with portable storage references."""
from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

from spr.db import File, Record, Run
from spr.paths import portable_origin, portable_snapshot, storage_ref

revision = '0002'
down_revision = '0001'


def upgrade():
    with Session(bind=op.get_bind()) as session:
        for file in session.scalars(select(File)):
            file.path = storage_ref('sources', file.project, file.dataset, file.id).as_posix()
        for run in session.scalars(select(Run)):
            if run.artifact:
                run.artifact = storage_ref('artifacts', run.project, run.dataset, run.id).as_posix()
            run.snapshot = portable_snapshot(run.snapshot, run.project, run.dataset)
        for dataset in session.scalars(select(Record).where(Record.kind == 'dataset')):
            dataset.body = portable_origin(dataset.body)
        session.flush()


def downgrade():
    # Portable references are valid in the original schema.
    pass
