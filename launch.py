"""One entry point on Windows, macOS and Linux. Requires Python 3.12."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='Контур: одинаковый локальный запуск на всех ОС')
    parser.add_argument('command', choices=['install', 'start', 'reset-login', 'test'])
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Используйте Python 3.12 на всех платформах.')
    os.chdir(ROOT)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               POLARS_MAX_THREADS='4', OMP_NUM_THREADS='1', SPR_TRUSTED_LOCAL_CODE='1')
    def run(*command):
        subprocess.run([str(python), '-X', 'utf8', *command], cwd=ROOT, env=env, check=True)
    if args.command == 'install':
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
        run('-m', 'pip', 'install', '-r', 'requirements.txt')
        run('-m', 'spr.cli', 'init')
        print('Установлено. Запуск: python launch.py start')
        return
    if not python.exists():
        parser.error('Сначала выполните python launch.py install')
    if args.command == 'reset-login':
        run('-m', 'spr.cli', 'local-reset')
    elif args.command == 'test':
        # Isolation tests must not mistake trusted execution for a sandbox.
        env.pop('SPR_TRUSTED_LOCAL_CODE', None)
        run('-m', 'pytest', '-q')
    else:
        with socket.socket() as probe:
            try:
                probe.bind((args.host, 8765))
            except OSError:
                parser.error('Порт 8765 занят. Возможно, приложение уже запущено: http://localhost:8765')
        print('http://localhost:8765 — доверенный локальный Python, без песочницы ОС', flush=True)
        try:
            run('-m', 'spr.cli', 'serve', '--host', args.host)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
