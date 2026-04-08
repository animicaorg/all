# ENA Studio Integration

ENA is still CLI-first, but the HTTP surface now exposes the main operator state needed for Studio or other dashboards.

## HTTP API

`animica ena serve` exposes:

- `GET /v1/health`
- `GET /v1/config`
- `GET /v1/sessions`
- `GET /v1/sessions/{session_id}`
- `GET /v1/artifacts`
- `GET /v1/datasets`
- `GET /v1/indexes`
- `GET /v1/jobs`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/receipt`
- `GET /v1/receipts`
- `GET /v1/memory?query=...`
- `GET /v1/evals`
- `GET /v1/training/runs`
- `GET /v1/training/runs/{run_id}`
- `POST /v1/ask`

## Studio Use Cases

This is enough to build:

- session browser and trace inspector
- artifact and receipt browser
- retrieval index browser
- useful-work job board
- dataset browser
- eval history view
- training run history and status view
- “run task” panel backed by `/v1/ask`

## Local State Model

The ENA SQLite store now tracks:

- sessions
- traces
- artifacts
- memory
- chunks and index metadata
- datasets
- jobs
- job events
- receipts
- eval runs
- training runs

Studio does not need a separate sidecar database to observe ENA state.
