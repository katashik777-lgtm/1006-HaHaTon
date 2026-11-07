"""Persistent Python sessions owned by an individual user."""
import ast
import atexit
import contextlib
import io
import json
import multiprocessing
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

if __package__:
    from .runner import hosted_command, hosted_env, hosted_isolation
else:
    sys.path.insert(0, str(Path(__file__).parent))
    from runner import hosted_command, hosted_env, hosted_isolation


def evaluate(code, scope):
    out, err, result = io.StringIO(), io.StringIO(), {}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            tree = ast.parse(code)
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                expression = tree.body.pop()
                exec(compile(tree, 'cell', 'exec'), scope)
                value = eval(compile(ast.Expression(expression.value), 'cell', 'eval'), scope)
                if value is not None:
                    result['value'] = repr(value)[:16000]
            else:
                exec(compile(tree, 'cell', 'exec'), scope)
    except BaseException:
        import traceback
        result['error'] = traceback.format_exc()[-16000:]
    return dict(result, stdout=out.getvalue()[-16000:], stderr=err.getvalue()[-16000:])


def kernel(pipe):
    scope = {'__name__': '__notebook__'}
    while True:
        pipe.send(evaluate(pipe.recv(), scope))


def hosted_kernel():
    scope = {'__name__': '__notebook__'}
    for line in sys.stdin:
        print(json.dumps(evaluate(json.loads(line), scope)), flush=True)


sessions = {}
lock = threading.Lock()


def close(key):
    item = sessions.pop(key, None)
    if not item:
        return
    process = item['process']
    if 'scratch' in item:
        import os
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()
        item['scratch'].cleanup()
    else:
        process.terminate()
        process.join(timeout=2)
        item['pipe'].close()


def create_session():
    if hosted_isolation():
        scratch = tempfile.TemporaryDirectory(prefix='spr-notebook-')
        try:
            process = subprocess.Popen(
                hosted_command(Path(__file__), scratch.name, None, 'notebook'),
                env=hosted_env(scratch.name), cwd=scratch.name, start_new_session=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8',
            )
        except BaseException:
            scratch.cleanup()
            raise
        return {'process': process, 'scratch': scratch}
    parent, child = multiprocessing.Pipe()
    process = multiprocessing.Process(target=kernel, args=(child,), daemon=True)
    process.start()
    child.close()
    return {'pipe': parent, 'process': process}


def hosted_result(process, code):
    result = {}

    def exchange():
        try:
            process.stdin.write(json.dumps(code) + '\n')
            process.stdin.flush()
            value = json.loads(process.stdout.readline(300000))
            if isinstance(value, dict) and all(
                key in ('stdout', 'stderr', 'error', 'value') and isinstance(text, str)
                and len(text) <= 16000 for key, text in value.items()
            ):
                result.update(value)
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=exchange, daemon=True)
    thread.start()
    thread.join(25)
    return result if not thread.is_alive() and result else None


def execute(owner, session, code, reset=False):
    with lock:
        for key, item in list(sessions.items()):
            if time.monotonic() - item['time'] > 1800 and not item['lock'].locked():
                close(key)
        key = (owner, session)
        if reset:
            close(key)
            return {'session': session}
        if key not in sessions:
            if len(sessions) >= (2 if hosted_isolation() else 16):
                raise ValueError('Слишком много активных сеансов')
            sessions[key] = {**create_session(), 'lock': threading.Lock()}
        item = sessions[key]
        item['time'] = time.monotonic()
    with item['lock']:
        if 'scratch' in item:
            result = hosted_result(item['process'], code)
        else:
            item['pipe'].send(code)
            result = item['pipe'].recv() if item['pipe'].poll(25) else None
        if result is None:
            with lock:
                if sessions.get(key) is item:
                    close(key)
            return {'error': 'Сеанс достиг лимита или завершился. Запусти ячейки заново.'}
        return result


@atexit.register
def cleanup():
    for key in list(sessions):
        close(key)


if __name__ == '__main__':
    hosted_kernel()
