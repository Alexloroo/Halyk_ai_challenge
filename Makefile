PYTHON ?= python3
DATA_DIR ?= data/raw
OUTPUT ?= submission.json
TRACE_DIR ?= trace
ARGS ?=
COMPOSE ?= docker compose
DOCKER_SERVICE ?= halyk

.PHONY: run fulltrace docker-build run-local fulltrace-local test lint

docker-build:
	$(COMPOSE) build $(DOCKER_SERVICE)

run: docker-build
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" $(COMPOSE) run --rm $(DOCKER_SERVICE) \
		make run-local DATA_DIR="$(DATA_DIR)" OUTPUT="$(OUTPUT)" ARGS="$(ARGS)"

fulltrace: docker-build
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" $(COMPOSE) run --rm $(DOCKER_SERVICE) \
		make fulltrace-local DATA_DIR="$(DATA_DIR)" OUTPUT="$(OUTPUT)" TRACE_DIR="$(TRACE_DIR)" ARGS="$(ARGS)"

run-local:
	$(PYTHON) -m halyk --data-dir "$(DATA_DIR)" --output "$(OUTPUT)" $(ARGS)

fulltrace-local:
	$(PYTHON) -m halyk --data-dir "$(DATA_DIR)" --output "$(OUTPUT)" --trace-dir "$(TRACE_DIR)" --fulltrace $(ARGS)

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
