#!/usr/bin/env bash
# Задать вопрос по ковенантам.  Пример:  ./ask.sh "Соблюдает ли Alpha Trade лимит за апрель 2026?"
cd "$(dirname "$0")"
PYTHONIOENCODING=utf-8 ./.venv311/Scripts/python.exe -m halyk_covenants.cli ask "$@" --db "${HALYK_DB:-data/check/c.duckdb}"
