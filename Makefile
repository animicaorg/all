# Top-level developer Makefile for common tasks
.PHONY: test relayer-test bench run-wallet fmt precommit-install
.PHONY: compute-dev compute-test compute-lint compute-down

test:
	python -m pytest -q

relayer-test:
	python -m pytest tests/integration/test_payout_relayer.py -q

bench:
	cd native/pq_precompile && cargo build --release --features with-oqs || true && \ 
	if [ -f target/release/bench ]; then ./target/release/bench > ../../bench_output.jsonl || true; else echo "bench not available"; fi

run-wallet:
	powershell -ExecutionPolicy Bypass -File wallet/run-wallet.ps1

fmt:
	black .
	isort .

precommit-install:
	python -m pip install pre-commit
	pre-commit install

# Animica Compute Platform commands
compute-dev:
	@echo "Starting Animica Compute Platform in development mode..."
	docker-compose -f docker-compose.compute.yml up -d
	@echo "Services available at:"
	@echo "  API Gateway:     http://localhost:8000"
	@echo "  Auth Service:    http://localhost:8001"
	@echo "  Billing Service: http://localhost:8002"
	@echo "  Inference:       http://localhost:8003"
	@echo "  Sandbox:         http://localhost:8004"
	@echo "  GitHub App:      http://localhost:8005"
	@echo "  Model Registry:  http://localhost:8006"
	@echo "  Web App:         http://localhost:3000"
	@echo "  MinIO Console:   http://localhost:9001"
	@echo "  RabbitMQ Mgmt:   http://localhost:15672"

compute-down:
	@echo "Stopping Animica Compute Platform..."
	docker-compose -f docker-compose.compute.yml down

compute-logs:
	docker-compose -f docker-compose.compute.yml logs -f

compute-test:
	@echo "Running compute platform tests..."
	pytest packages/*/tests/ -v

compute-lint:
	@echo "Linting compute platform code..."
	black packages/
	isort packages/
	ruff check packages/ --fix

compute-clean:
	@echo "Cleaning compute platform volumes..."
	docker-compose -f docker-compose.compute.yml down -v
