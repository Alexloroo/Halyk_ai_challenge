@echo off
REM Обработать папку input\ целиком.  Пример:  .\run.bat
setlocal
set PYTHONIOENCODING=utf-8
set PROJ=%~dp02_ARCHITECTURE_COVENANT_MVP
pushd "%PROJ%"
"%PROJ%\.venv311\Scripts\python.exe" -m halyk_covenants.cli run %*
popd
