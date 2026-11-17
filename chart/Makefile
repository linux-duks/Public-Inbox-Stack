# Makefile for the public-inbox-stack Helm chart.
#
# Kubernetes helpers that belong next to the chart. Run from the repository
# root with `make -C chart <target>`, or from this directory with `make <target>`.

.DEFAULT_GOAL := help

##@ Kubernetes

# The controller reads config overrides from the dotenv file at ENV_PATH; the
# chart mounts it from this Secret (key ".env"). Keep credentials in the
# repository-root .env (gitignored) - never in values.yaml.
# See README.md ("Secrets").
NAMESPACE ?= lore
SECRET ?= public-inbox-secrets
# Relative to this directory, so the default is the repository-root .env
SECRETS_ENV_FILE ?= .env
IMAP_KEY ?= IMAP_INBOXES_0_URL
KUBECTL ?= kubectl

# Create or update the dotenv Secret holding the IMAP inbox URL. Prompts for the
# URL (hidden input) unless IMAP_URL is exported:
#   IMAP_URL='imaps://user:pass@host:993/INBOX' make -C chart secrets/create
secrets/create: ## Create/update the dotenv Secret with the IMAP inbox URL
	@test -f $(SECRETS_ENV_FILE) || printf '# .env - secrets only (gitignored)\n' > $(SECRETS_ENV_FILE)
	@chmod 600 $(SECRETS_ENV_FILE)
	@if [ -n "$$IMAP_URL" ]; then echo "==> using IMAP_URL from the environment"; else \
		printf 'IMAP inbox URL (imaps://user:pass@host:993/INBOX): '; \
		stty -echo 2>/dev/null; read -r IMAP_URL < /dev/tty; stty echo 2>/dev/null; echo; \
	fi; \
	test -n "$$IMAP_URL" || { echo "aborted: empty value" >&2; exit 1; }; \
	tmp=$$(mktemp) || exit 1; \
	{ grep -v '^$(IMAP_KEY)=' $(SECRETS_ENV_FILE) || true; printf '%s=%s\n' '$(IMAP_KEY)' "$$IMAP_URL"; } > $$tmp; \
	cat $$tmp > $(SECRETS_ENV_FILE); rm -f $$tmp; \
	echo "==> wrote $(IMAP_KEY) to $(SECRETS_ENV_FILE)"
	@yaml=$$(mktemp) || exit 1; \
	$(KUBECTL) -n $(NAMESPACE) create secret generic $(SECRET) \
		--from-file=.env=$(SECRETS_ENV_FILE) \
		--dry-run=client -o yaml > $$yaml || { rm -f $$yaml; exit 1; }; \
	echo "==> generated Secret:"; cat $$yaml; \
	$(KUBECTL) apply -f $$yaml; status=$$?; rm -f $$yaml; exit $$status

##@ Help

help: ## Show this help
	@echo "Usage: make -C chart [target]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z0-9_/-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

.PHONY: secrets/create help
