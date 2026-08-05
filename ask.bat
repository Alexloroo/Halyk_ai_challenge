@echo off
REM Задать вопрос по ковенантам.  Пример:  .\ask.bat "Соблюдает ли Alpha Trade лимит за апрель 2026?"
setlocal
set PYTHONIOENCODING=utf-8
set PROJ=%~dp02_ARCHITECTURE_COVENANT_MVP
if "%HALYK_DB%"=="" set HALYK_DB=data\check\c.duckdb
pushd "%PROJ%"
"%PROJ%\.venv311\Scripts\python.exe" -m halyk_covenants.cli ask %* --db "%HALYK_DB%"
popd
