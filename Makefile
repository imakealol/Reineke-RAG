# ------------------------------------------------------------
# Reineke-RAG — top-level Makefile (SKELETON)
# deployment-agent fills this in during Phase 1.
# ------------------------------------------------------------
.DEFAULT_GOAL := help

COMPOSE       ?= docker compose
COMPOSE_FILE  ?= config/docker-compose.yml
ENV_FILE      ?= .env
PROJECT       ?= reineke
PROFILES      ?=

CP := $(COMPOSE) -f $(COMPOSE_FILE) --env-file $(ENV_FILE) -p $(PROJECT) $(if $(PROFILES),--profile $(PROFILES),)

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: bootstrap
bootstrap: ## First-time setup: generate .env, owner-inputs, dirs
	bash scripts/bootstrap.sh

.PHONY: pull
pull: ## Pull all container images
	$(CP) pull

.PHONY: pull-models
pull-models: ## Pull LLM + embedding + reranker models
	$(CP) --profile init up --abort-on-container-exit ollama-init

.PHONY: up
up: ## Start the core stack
	$(CP) up -d

.PHONY: up-automation
up-automation: ## Start the stack + n8n + watcher
	$(CP) --profile automation up -d

.PHONY: down
down: ## Stop everything
	$(CP) down

.PHONY: restart
restart: ## Restart all services
	$(CP) restart

.PHONY: ps
ps: ## List services
	$(CP) ps

.PHONY: logs
logs: ## Tail all logs
	$(CP) logs -f --tail=200

.PHONY: wait-healthy
wait-healthy: ## Block until all healthchecks are green (2-5 min first time)
	bash scripts/wait-healthy.sh

.PHONY: smoke
smoke: ## Run smoke tests for models + APIs
	bash scripts/smoke-llm.sh
	bash scripts/smoke-embed.sh
	bash scripts/smoke-rerank.sh

.PHONY: backup
backup: ## Run a manual backup now
	bash scripts/backup.sh

.PHONY: restore-plan
restore-plan: ## Dry-run restore of the most recent backup
	bash scripts/restore.sh --plan

.PHONY: eval
eval: ## Run the retrieval evaluation against the gold query set
	python scripts/eval.py --gold config/eval/gold-queries.yaml

.PHONY: lint
lint: ## Lint compose + yaml + python
	docker run --rm -v $(PWD):/work -w /work hadolint/hadolint:latest /bin/sh -c "hadolint services/*/Dockerfile"
	yamllint config/
	ruff check services/

.PHONY: clean
clean: ## Remove containers but keep volumes
	$(CP) down
