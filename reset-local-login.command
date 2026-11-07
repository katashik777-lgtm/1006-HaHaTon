#!/bin/sh
cd "$(dirname "$0")" || exit 1
.venv/bin/python launch.py reset-login
printf '\nНажмите Enter для закрытия окна…'
read -r answer
