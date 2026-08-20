# Include shared container runtime detection
include ./containers.mk

# Absolute path to the controller console script inside the container
PI_CONTROLLER = /pistack-controller/.venv/bin/pistack-controller

# Dev mode: run the controller from the mounted source with uv (no rebuild needed)
DEV_COMPOSE = $(COMPOSE) -f compose.dev.yaml
DEV_CONTROLLER = uv run --project /pistack-controller pistack-controller

.PHONY: run watch stop logs build rebuild dev dev-watch index purge obfuscate render help

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
	$(COMPOSE) up -d

dev: ## Start the stack in dev mode (source mounted, uv run)
	$(DEV_COMPOSE) up -d

dev-watch: ## Start the stack in dev mode in the foreground
	$(DEV_COMPOSE) up

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

##@ Help

help: ## Show this help
	@echo "Usage: make [target]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
