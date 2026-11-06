#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  .venv/bin/python launch.py start "$@"
else
  echo "Сначала откройте install-local.command"
fi
printf '\nНажмите Enter для закрытия окна…'
read -r answer
