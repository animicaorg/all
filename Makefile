# Top-level developer Makefile for common tasks
.PHONY: test relayer-test bench run-wallet fmt precommit-install

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
