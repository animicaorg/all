# Animica Ops Makefile
# Targets:
#   - devnet-up     : start docker-compose devnet (uses tests/devnet/*)
#   - devnet-down   : stop devnet
#   - devnet-clean  : stop devnet and remove volumes
#   - ps            : docker compose ps
#   - logs          : tail compose logs
#   - k8s-apply     : kubectl apply -f ops/k8s (if present)
#   - helm-devnet   : helm upgrade --install from ops/charts/devnet (if present)
#   - metrics-up    : start Prometheus/Grafana if ops/metrics/docker-compose.yml exists

SHELL := /bin/bash

DOCKER ?= docker
COMPOSE ?= $(DOCKER) compose

# --- Devnet (docker compose) ---------------------------------------------------

DEVNET_COMPOSE        := tests/devnet/docker-compose.yml
DEVNET_ENV_PRIMARY    := tests/devnet/env.devnet
DEVNET_ENV_FALLBACK   := tests/devnet/env.devnet.example
WAIT_SCRIPT           := tests/devnet/wait_for_services.sh

define SELECT_ENV
@if [ -f "$(DEVNET_ENV_PRIMARY)" ]; then \
  export ENV_FILE="$(DEVNET_ENV_PRIMARY)"; \
elif [ -f "$(DEVNET_ENV_FALLBACK)" ]; then \
  export ENV_FILE="$(DEVNET_ENV_FALLBACK)"; \
else \
  echo "No env file found. Create $(DEVNET_ENV_PRIMARY) (see $(DEVNET_ENV_FALLBACK))."; \
  exit 1; \
fi; \
echo "Using $$ENV_FILE";
endef

.PHONY: devnet-up
devnet-up:
	@echo "==> Starting Animica devnet via docker compose"
	$(SELECT_ENV)
	@if [ ! -f "$(DEVNET_COMPOSE)" ]; then \
	  echo "Missing $(DEVNET_COMPOSE)."; \
	  exit 1; \
	fi
	$(COMPOSE) --env-file $$ENV_FILE -f $(DEVNET_COMPOSE) up -d
	@if [ -x "$(WAIT_SCRIPT)" ]; then \
	  echo "==> Waiting for services to become ready..."; \
	  $(WAIT_SCRIPT); \
	else \
	  echo "==> Note: $(WAIT_SCRIPT) not found or not executable; skipping readiness wait."; \
	fi
	@echo "==> Devnet is up."
	@echo "    RPC HTTP : http://localhost:$${RPC_HTTP_PORT:-8545}/rpc"
	@echo "    RPC WS   : ws://localhost:$${RPC_WS_PORT:-8546}/ws"
	@echo "    Explorer : http://localhost:$${EXPLORER_PORT:-8081}/"
	@echo "    Studio   : http://localhost:$${STUDIO_PORT:-8082}/"

.PHONY: devnet-down
devnet-down:
	@echo "==> Stopping Animica devnet"
	$(SELECT_ENV)
	-$(COMPOSE) --env-file $$ENV_FILE -f $(DEVNET_COMPOSE) down
	@echo "==> Stopped."

.PHONY: devnet-clean
devnet-clean:
	@echo "==> Stopping devnet and removing volumes"
	$(SELECT_ENV)
	-$(COMPOSE) --env-file $$ENV_FILE -f $(DEVNET_COMPOSE) down -v
	@echo "==> Done."

.PHONY: ps
ps:
	$(SELECT_ENV)
	$(COMPOSE) --env-file $$ENV_FILE -f $(DEVNET_COMPOSE) ps

.PHONY: logs
logs:
	$(SELECT_ENV)
	$(COMPOSE) --env-file $$ENV_FILE -f $(DEVNET_COMPOSE) logs -f

# --- Kubernetes (optional) -----------------------------------------------------

K8S_DIR ?= ops/k8s

.PHONY: k8s-apply
k8s-apply:
	@echo "==> Applying Kubernetes manifests from $(K8S_DIR)"
	@if [ -d "$(K8S_DIR)" ]; then \
	  kubectl apply -f $(K8S_DIR); \
	else \
	  echo "Directory $(K8S_DIR) not found. Create it with your manifests."; \
	fi

# --- Helm (optional) -----------------------------------------------------------

CHART_DIR ?= ops/charts/devnet
RELEASE   ?= animica-devnet
NAMESPACE ?= animica-devnet
VALUES    ?= values.devnet.yaml

.PHONY: helm-devnet
helm-devnet:
	@echo "==> Helm deploy $(RELEASE) into namespace $(NAMESPACE)"
	@if [ -d "$(CHART_DIR)" ]; then \
	  if [ -f "$(CHART_DIR)/$(VALUES)" ]; then \
	    helm upgrade --install $(RELEASE) $(CHART_DIR) -n $(NAMESPACE) --create-namespace -f $(CHART_DIR)/$(VALUES); \
	  else \
	    helm upgrade --install $(RELEASE) $(CHART_DIR) -n $(NAMESPACE) --create-namespace; \
	  fi; \
	else \
	  echo "Chart dir $(CHART_DIR) not found. Skipping."; \
	fi

# --- Metrics stack (optional) --------------------------------------------------

METRICS_COMPOSE ?= ops/metrics/docker-compose.yml

.PHONY: metrics-up
metrics-up:
	@echo "==> Starting metrics stack (Prometheus/Grafana)"
	@if [ -f "$(METRICS_COMPOSE)" ]; then \
	  $(COMPOSE) -f $(METRICS_COMPOSE) up -d; \
	  echo "Grafana:   http://localhost:3000  | Prometheus: http://localhost:9090"; \
	else \
	  echo "$(METRICS_COMPOSE) not found. Create it to run metrics locally."; \
	fi
