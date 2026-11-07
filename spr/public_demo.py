"""Public sign-in for one published, open-data report."""
import os
import secrets

from sqlalchemy import select

from .db import Run, Session, User
from .security import hash_password

VIEWER_ID = 'scorify-public-demo'


def demo_run(session, *, lock=False):
    run_id = os.environ.get('SPR_PUBLIC_DEMO_RUN')
    run = session.get(Run, run_id, with_for_update=lock) if run_id else None
    return run if run and run.status == 'completed' else None


def demo_repeats(base):
    return select(Run).where(Run.project == base.project, Run.dataset == base.dataset,
                             Run.author == VIEWER_ID, Run.snapshot['demo_base'].as_string() == base.id)


def is_demo_run(run, base):
    return run.id == base.id or (run.author == VIEWER_ID and run.dataset == base.dataset
                                 and run.snapshot.get('demo_base') == base.id)


def init_viewer():
    with Session.begin() as session:
        if demo_run(session) and not session.get(User, VIEWER_ID):
            session.add(User(id=VIEWER_ID, name='Демо-просмотр', admin=0,
                             password=hash_password(secrets.token_urlsafe(32))))
