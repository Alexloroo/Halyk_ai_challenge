@echo off
REM Задать вопрос по ковенантам.  Пример:  ask "Соблюдает ли Alpha Trade лимит за апрель 2026?"
setlocal
set PYTHONIOENCODING=utf-8
if "%HALYK_DB%"=="" set HALYK_DB=data\check\c.duckdb
"%~dp0.venv311\Scripts\python.exe" -m halyk_covenants.cli ask %* --db "%HALYK_DB%"
