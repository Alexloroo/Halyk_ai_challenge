@echo off
REM Обработать папку input\ целиком.  Пример:  .\run.bat
setlocal
set PYTHONIOENCODING=utf-8
"%~dp0.venv311\Scripts\python.exe" -m halyk_covenants.cli run %*
