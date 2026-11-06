#!/bin/sh
cd "$(dirname "$0")" || exit 1
python3 launch.py install --local-demo
printf '\nНажмите Enter для закрытия окна…'
read -r answer
