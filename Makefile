# Include shared container runtime detection
include ./containers.mk

# Absolute path to the controller console script inside the container
PI_CONTROLLER = /pistack-controller/.venv/bin/pistack-controller

# Dev mode: run the controller from the mounted source with uv (no rebuild needed)
DEV_COMPOSE = $(COMPOSE) -f compose.dev.yaml
DEV_CONTROLLER = uv run --project /pistack-controller pistack-controller
PROXY_COMPOSE = -f proxy.compose.yaml

.PHONY: run watch stop logs build rebuild dev dev-watch publish index purge obfuscate render proxy-config proxy-config-dry-run help
.PHONY: integration-test integration-test-smoke integration-test-clean

##@ Lifecycle

run: ## Start the stack (controller container)
	$(COMPOSE) up -d

watch: ## Start the stack in the foreground
	$(COMPOSE) up

stop: ## Stop all services
	$(COMPOSE) down

logs: ## Show logs for all services
	$(COMPOSE) logs -f

build: ## Build the controller image
	$(COMPOSE) build

rebuild: ## Rebuild the controller image from scratch (no cache) and restart
	$(COMPOSE) build --no-cache
	$(COMPOSE) down

dev: ## Start the stack in dev mode (source mounted, uv run)
	$(DEV_COMPOSE) up -d

dev-watch: ## Start the stack in dev mode in the foreground
	$(DEV_COMPOSE) up

## run with the aditional proxy compose
run-with-proxy: run
	$(COMPOSE) --env-file proxy/proxy.config -f compose.yaml $(PROXY_COMPOSE) up -d

##@ Publish

# Registry used when publishing the image. Empty by default: the image is
# pushed to whatever registry the user's podman/docker is configured for
# (e.g. docker.io or the registry they are logged into). Override with:
#   make publish REGISTRY=registry.oci.internal
REGISTRY ?=
IMAGE ?= public-inbox-stack
IMAGE_TAG ?= $(shell git describe --tags --always 2>/dev/null || echo latest)

publish: ## Build and push the image to an OCI registry
	@if [ -z "$(REGISTRY)" ]; then \
		printf "Registry name (e.g. registry.oci.internal), empty to use your default registry: " >&2; \
		read -r REGISTRY < /dev/tty || true; \
	fi; \
	REGISTRY="$${REGISTRY:-$(REGISTRY)}"; \
	BASE="$${REGISTRY:+$$REGISTRY/}$(IMAGE)"; \
	IMG="$$BASE:$(IMAGE_TAG)"; \
	echo "==> Building $$IMG"; \
	$(CONTAINER) build \
		--build-arg USER_ID=$(UID) \
		--build-arg GROUP_ID=$(GID) \
		-t "$$IMG" \
		-t "$$BASE:latest" \
		-f Containerfile .; \
	echo "==> Pushing $$IMG"; \
	$(CONTAINER) push "$$IMG"; \
	echo "==> Pushing $$BASE:latest"; \
	$(CONTAINER) push "$$BASE:latest"

##@ Proxy

# Render proxy/angie.conf from the jinja2 template + proxy.config.
# Uses uv natively when available; otherwise falls back to running uv from
# the ghcr.io/astral-sh/uv container image.
define run_proxy_render
	@if command -v uv >/dev/null 2>&1; then \
		echo "==> Found uv toolchain, running natively..."; \
		uv run --project pistack-controller python proxy/render_proxy.py $(1); \
	else \
		echo "==> uv toolchain not found, running with $(CONTAINER) (Image: ghcr.io/astral-sh/uv:debian)..."; \
		$(CONTAINER) run --rm -it \
			-v "$(CURDIR):/app" \
			-w /app \
			ghcr.io/astral-sh/uv:debian \
			uv run --project pistack-controller python proxy/render_proxy.py $(1); \
	fi
endef

proxy-config: ## Render proxy/angie.conf from proxy/angie.conf.j2 + proxy.config
	$(call run_proxy_render)

proxy-config-dry-run: ## Print the rendered angie.conf without writing it
	$(call run_proxy_render,--dry-run)

##@ One-shot operations (run inside the controller container)

render: ## Render grokmirror + public-inbox configs
	$(COMPOSE) run --rm public-inbox-stack $(PI_CONTROLLER) render

dev-render: ## Render configs (dev)
	$(DEV_COMPOSE) run --rm public-inbox-stack $(DEV_CONTROLLER) render

index: ## Index cloned repos (+ extindex)
	$(COMPOSE) run --rm public-inbox-stack $(PI_CONTROLLER) index

dev-index: ## Index cloned repos (dev)
	$(DEV_COMPOSE) run --rm public-inbox-stack $(DEV_CONTROLLER) index

purge: ## Purge indexing data (preserves grokmirror clones)
	$(COMPOSE) run --rm public-inbox-stack $(PI_CONTROLLER) purge

dev-purge: ## Purge indexing data (dev)
	$(DEV_COMPOSE) run --rm public-inbox-stack $(DEV_CONTROLLER) purge

obfuscate: ## Add obfuscate = true to all publicinbox sections missing it
	$(COMPOSE) run --rm public-inbox-stack $(PI_CONTROLLER) obfuscate

dev-obfuscate: ## Add obfuscate = true to all publicinbox sections (dev)
	$(DEV_COMPOSE) run --rm public-inbox-stack $(DEV_CONTROLLER) obfuscate

##@ Tests

integration-test: ## Mirror + index 360 generated lists end to end (docker compose or podman-compose)
	$(MAKE) -C tests/integration integration-test

integration-test-smoke: ## Fast integration run with 20 generated lists
	$(MAKE) -C tests/integration integration-test-smoke

integration-test-clean: ## Remove integration-test containers and state
	$(MAKE) -C tests/integration integration-test-clean

##@ Help

help: ## Show this help
	@echo "Usage: make [target]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
