# Changelog

## 0.1.0 — initial release

- npm-installable `animica-node` operator CLI.
- Real backend orchestration: resolves `animica` PATH binary, repo `.venv`
  Python, or `python3 -m animica.cli.main` in that order.
- Daemon manager (start/stop/restart) with pidfile + log files under
  `~/.animica/node/`.
- `doctor`, `status`, `logs`, `peers`, `sync status`, `config show|set`.
- RPC pass-through, miner-safety hints, agent discovery hook.
- 3-test vitest suite.
