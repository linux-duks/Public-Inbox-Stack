# Integration tests for the mirroring + indexing pipeline.
#
#   make integration-test              # all three scenarios (large scale)
#   make integration-test-smoke        # all three scenarios (small scale)
#   make integration-test-baseline     # mirror+index 360 lists
#   make integration-test-incremental  # new lists + new messages, 2nd cycle
#   make integration-test-concurrency  # pull overlapping an index run
#   make integration-check             # re-run assertions on a KEEP=1 stack
#   make integration-test-clean        # remove containers and state
#
# Scales are overridable, e.g.
#   make integration-test-baseline LIST_COUNT=20
#   make integration-test-incremental INC_LIST_COUNT=20 ADD_LIST_COUNT=5
#   make integration-test-concurrency CONC_LIST_COUNT=30 CONC_INTERVAL=3
#
# Works with docker compose (v2) and podman-compose: containers.mk detects the
# runtime and run.sh adds the podman-only overrides (compose.podman.yaml)
# automatically. Force one explicitly with COMPOSE="podman-compose".

include ../../containers.mk

# Defaults for the knobs that apply to every scenario. Scenario list/message
# counts are NOT defaulted here on purpose: run.sh owns the per-profile
# defaults, and an exported LIST_COUNT would override them for the baseline.
INDEX_LEVEL ?= basic
ITEST_TIMEOUT ?= 2400
ITEST_OVERLAP_TIMEOUT ?= 180

export INDEX_LEVEL ITEST_TIMEOUT ITEST_OVERLAP_TIMEOUT

.PHONY: integration-test integration-test-smoke integration-test-baseline \
	integration-test-incremental integration-test-concurrency \
	integration-check integration-test-clean

integration-test: ## Run baseline + incremental + concurrency scenarios
	COMPOSE="$(COMPOSE)" ./run.sh full

integration-test-smoke: ## Same three scenarios with small list counts
	COMPOSE="$(COMPOSE)" ./run.sh smoke

integration-test-baseline: ## Baseline: mirror + index N lists and serve them
	COMPOSE="$(COMPOSE)" ./run.sh baseline

integration-test-incremental: ## New lists/messages picked up by a second cycle
	COMPOSE="$(COMPOSE)" ./run.sh incremental

integration-test-concurrency: ## grok-pull overlapping an index/extindex run
	COMPOSE="$(COMPOSE)" ./run.sh concurrency

integration-check: ## Re-run the assertions against a running (KEEP=1) stack
	COMPOSE="$(COMPOSE)" ./run.sh check

integration-test-clean: ## Remove the integration-test containers and state
	COMPOSE="$(COMPOSE)" ./run.sh clean
