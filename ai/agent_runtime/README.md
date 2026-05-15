# agent_runtime

Distributed AICF chat client + shared primitives.

## What it provides

- `agent_runtime.config` — hierarchical YAML+env config loader with strict
  validation, placeholder detection, and run-snapshot writing. Shared with
  `flagship_agent`.
- `agent_runtime.logging` — structured logging, stage banners, per-stage log
  files, manifest emission.
- `agent_runtime.aicf_client` — submits inference jobs to AICF, debits the
  configured wallet, polls and streams results.
- `agent_runtime.wallet` — loads a configured wallet, shows balance, signs
  AICF payment transactions, surfaces pre-flight cost previews.
- `agent_runtime.providers` — provider abstraction: `distributed-aicf`,
  `local-flagship`, `offline`.
- `agent_runtime.planner` — view classifier + inference-time prompt
  structuring from `ai/configs/planning.yaml`.
- `agent_runtime.cli.chat` — the `animica chat` REPL.

## Entry points

- `animica chat` — top-level CLI command registered in the animica python
  CLI (`animica.cli.main`), which delegates to
  `agent_runtime.cli.chat:main`.
- `animica-chat` — the same command, exposed as a standalone console_script
  for users who installed `agent_runtime` without the full animica wheel.

## Provider selection at runtime

Order from `ai/configs/integration.yaml::agent_runtime.provider_order`,
default: `distributed-aicf → local-flagship → offline`.

Failures cascade: each provider in order is asked `is_available()`. The
first that returns true is used. Silent fallback can be disabled by setting
`ANIMICA_CHAT_REQUIRE_DISTRIBUTED=1` (refuses to fall back from
`distributed-aicf`).

## Honesty

Every assistant turn returned by the REPL carries metadata:

- `provider`              which provider answered
- `tier`                  model tier (from `ai/configs/model_catalog.yaml`)
- `requested_tier`        what the user asked for (or default)
- `effective_mode`        the provider's effective mode (real/lite/simulate)
- `cost_animica`          ANIMICA spent (0.0 for offline / local)
- `fallback_reasons`      list of reasons earlier providers were skipped
