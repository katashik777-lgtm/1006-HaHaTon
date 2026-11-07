"""Start the public API and its worker with an initialized database."""
import os
import signal
import subprocess
import sys
import time


def verify_hosted_python():
    if os.environ.get('SPR_HOST_ISOLATION') != '1':
        return
    from spr.config import DATA
    from spr.db import Session
    from spr.paths import storage_path
    from spr.public_demo import demo_run
    from spr.runner import capability, execute

    with Session() as session:
        run = demo_run(session)
        artifact = storage_path(DATA, 'artifacts', run.project, run.dataset, run.id) if run else None
    if not capability():
        print('Python isolation unavailable', flush=True)
        os.environ.pop('SPR_HOST_ISOLATION', None)
        return
    if artifact:
        code = '''
import os, socket
def run(mart, params):
    assert 'DATABASE_URL' not in os.environ
    for attempt in (lambda: open('/proc/1/environ'), lambda: socket.socket()):
        try:
            attempt()
        except PermissionError:
            continue
        raise RuntimeError('Isolation probe failed')
    assert mart.total_apps() > 0
    return []
CHECK = {'entrypoint': run}
'''
        outcome = execute(code, {}, artifact)
        if outcome.get('rows') != []:
            print('Python isolation could not open the demo report:', outcome.get('error'),
                  'exit', outcome.get('exit_code'), flush=True)
            os.environ.pop('SPR_HOST_ISOLATION', None)
            return
    print('Hosted Python isolation verified', flush=True)


def main():
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL is required')
    if os.environ.get('RAILWAY_ENVIRONMENT') and not os.environ.get('RAILWAY_VOLUME_MOUNT_PATH'):
        raise SystemExit('Attach a persistent volume before starting the app')
    if mount := os.environ.get('RAILWAY_VOLUME_MOUNT_PATH'):
        os.environ['SPR_DATA'] = mount
    if domain := os.environ.get('RAILWAY_PUBLIC_DOMAIN'):
        os.environ.setdefault('BASE_URL', 'https://' + domain)

    # Public deployments must never run uploaded Python in trusted local mode.
    os.environ.pop('SPR_TRUSTED_LOCAL_CODE', None)
    password = os.environ.pop('SPR_ADMIN_PASSWORD', '')
    if password != 'admin' and len(password) < 12:
        raise SystemExit('Set SPR_ADMIN_PASSWORD to admin or a password of at least 12 characters')

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from spr.cli import init
    from spr.db import engine

    for _ in range(45):
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
            break
        except OperationalError:
            time.sleep(2)
    else:
        raise SystemExit('Could not connect to the metadata database')

    init(password, sync_admin_password=True)
    from spr.public_demo import init_viewer
    init_viewer()
    verify_hosted_python()
    if os.environ.get('SPR_LOAD_DEMO') == '1':
        subprocess.run([sys.executable, '-m', 'spr.cli', 'demo'], check=True)

    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, '-m', 'spr.worker']))
        children.append(subprocess.Popen([
            sys.executable, '-m', 'uvicorn', 'spr.api:app',
            '--host', os.environ.get('HOST', '0.0.0.0'),
            '--port', os.environ.get('PORT', '8765'),
        ]))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(.5)
        if not stopping:
            print('Service process ended:', [child.poll() for child in children], flush=True)
        return 0 if stopping else 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    sys.exit(main())
