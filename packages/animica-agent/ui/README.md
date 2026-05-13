# @animica/agent-ui

Tiny built-in HTTP bridge + static dashboard for the Animica Coding Agent.
Bound to `127.0.0.1` only, no auth, no remote endpoints. Started by
`animica-agent ui` (defaults: `http://127.0.0.1:4720`).

Endpoints:

- `GET /` — static HTML dashboard
- `GET /api/status` — JSON snapshot (project, node, wallet, miner)
- `GET /api/health` — `{"ok":true}`

## License

Apache-2.0.
