# Contributing to Animica Monorepo

Thanks for contributing! This file outlines how to set up a local development environment, run tests, and submit changes.

Getting started
1. Clone the repo and create a virtualenv for Python development:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

2. Install pre-commit hooks (recommended):

```powershell
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Testing
- Run unit tests:

```powershell
python -m pytest -q
```

- Run a single integration test (relayer):

```powershell
python -m pytest tests/integration/test_payout_relayer.py -q
```

Working with the Flutter wallet
- See `wallet/README.md` for a dedicated quickstart.
- Use `make run-wallet` from the repo root to run the wallet helper.

CI and Bench
- The `pq-precompile.yml` workflow saves bench stdout to `bench_output.jsonl` and pytest junit xml to `reports/junit.xml` as artifacts for review.

Submitting changes
- Follow the branching policy: create a branch per feature/fix and open a PR to `main`.
- Ensure pre-commit checks pass locally and CI is green.
- Add/update tests for new behaviors.

Contact
- For infra/CI questions, open an issue or contact the maintainers listed in `MAINTAINERS.md`.
