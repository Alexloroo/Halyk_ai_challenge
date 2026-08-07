PYTHON ?= python
DATA_DIR ?= data/raw
OUTPUT ?= submission.json
TRACE_DIR ?= trace
ARGS ?=

.PHONY: run fulltrace test lint

run:
	$(PYTHON) -m halyk --data-dir "$(DATA_DIR)" --output "$(OUTPUT)" $(ARGS)

fulltrace:
	$(PYTHON) -m halyk --data-dir "$(DATA_DIR)" --output "$(OUTPUT)" --trace-dir "$(TRACE_DIR)" --fulltrace $(ARGS)

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
