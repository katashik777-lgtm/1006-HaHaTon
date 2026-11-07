"""One entry point on Windows, macOS and Linux. Requires Python 3.12–3.14."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='Скорифай: одинаковый локальный запуск на всех ОС')
    parser.add_argument('command', choices=['install', 'start', 'reset-login', 'test'])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765, help='Порт локального приложения')
    parser.add_argument('--local-demo', action='store_true', help='Загрузить открытый учебный набор при установке')
    args = parser.parse_args()
    if not (3, 12) <= sys.version_info[:2] <= (3, 14):
        parser.error('Используйте Python 3.12, 3.13 или 3.14.')
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
        run('-m', 'spr.cli', 'local-init')
        if args.local_demo:
            run('-m', 'spr.cli', 'demo', '--if-available')
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
        if not 1 <= args.port <= 65535:
            parser.error('Укажите порт от 1 до 65535.')
        url = f'http://localhost:{args.port}'
        with socket.socket() as probe:
            # POSIX leaves closed connections in TIME_WAIT after a normal stop.
            if os.name != 'nt':probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((args.host, args.port))
            except OSError:
                parser.error(f'Не удалось открыть порт {args.port}. Проверьте {url} или выберите другой порт через --port.')
        run('-m', 'spr.cli', 'local-init')
        env.setdefault('BASE_URL', f'http://127.0.0.1:{args.port}')
        print(f'{url} — доверенный локальный Python, без песочницы ОС', flush=True)
        try:
            run('-m', 'spr.cli', 'serve', '--host', args.host, '--port', str(args.port))
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
