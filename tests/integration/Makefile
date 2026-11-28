# Integration tests for the mirroring + indexing pipeline.
#
#   make integration-test          # full run: 360 generated lists
#   make integration-test-smoke    # quick run: 20 generated lists
#   make integration-check         # re-run assertions on a kept stack (KEEP=1)
#   make integration-test-clean    # remove containers and state
#
# Works with docker compose (v2) and podman-compose: containers.mk detects the
# runtime and run.sh adds the podman-only overrides (compose.podman.yaml)
# automatically. Force one explicitly with COMPOSE="podman-compose".

include ../../containers.mk

LIST_COUNT ?= 360
MESSAGES_PER_LIST ?= 3
INDEX_LEVEL ?= basic
ITEST_TIMEOUT ?= 2400

export LIST_COUNT MESSAGES_PER_LIST INDEX_LEVEL ITEST_TIMEOUT

.PHONY: integration-test integration-test-smoke integration-check integration-test-clean

integration-test: ## Mirror + index 360 generated lists and assert all are served
	COMPOSE="$(COMPOSE)" ./run.sh run

integration-test-smoke: ## Same test with 20 lists (fast local check)
	COMPOSE="$(COMPOSE)" LIST_COUNT=20 ITEST_TIMEOUT=900 ./run.sh run

integration-check: ## Re-run the assertions against a running (KEEP=1) stack
	COMPOSE="$(COMPOSE)" ./run.sh check

integration-test-clean: ## Remove the integration-test containers and state
	COMPOSE="$(COMPOSE)" ./run.sh clean
