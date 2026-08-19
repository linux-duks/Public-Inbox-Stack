# Include shared container runtime detection
include ./containers.mk

.PHONY: run watch stop logs build index purge obfuscate render help

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

##@ One-shot operations (run inside the controller container)

render: ## Render grokmirror + public-inbox configs
	$(COMPOSE) run --rm public-inbox-stack pistack-controller render

index: ## Index cloned repos (+ extindex)
	$(COMPOSE) run --rm public-inbox-stack pistack-controller index

purge: ## Purge indexing data (preserves grokmirror clones)
	$(COMPOSE) run --rm public-inbox-stack pistack-controller purge

obfuscate: ## Add obfuscate = true to all publicinbox sections missing it
	$(COMPOSE) run --rm public-inbox-stack pistack-controller obfuscate

##@ Help

help: ## Show this help
	@echo "Usage: make [target]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
