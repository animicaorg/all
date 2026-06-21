# Animica Studio Qt

**Animica Studio Qt** is a polished, dark-themed desktop **agentic coding IDE** —
an AI chat + autonomous code builder — powered by **Animica inference** over the
OpenAI-compatible `/v1/chat/completions` API. It combines a project-aware chat
assistant, a file/editor workspace, a sandboxed agent that can read, write, patch,
search, run commands, and use git — all gated by an explicit, human-in-the-loop
safety and approval model.

Built with **PySide6** (Qt for Python), Python 3.11+, and SQLite for sessions,
messages, projects, settings, tool calls, command approvals, and the file index.

---

## Features

- **AI chat + agent** — streaming, markdown-rendered conversations with an expert
  autonomous engineering persona ("Animica Studio Agent").
- **Project-aware** — opens a folder as a sandboxed workspace, indexes files,
  builds compact context (summaries, file tree, relevant files, history) within a
  token budget.
- **Tooling** — read/write/patch/create/rename/delete files, list/search, run
  shell commands, git status/diff/commit, install dependencies, detect project
  type, summarize the project.
- **Diff viewer** — every proposed file change is shown old-vs-new with per-file
  accept/reject and a rollback snapshot before anything touches disk.
- **Safety first** — destructive / install / docker / deploy / wallet / network /
  privilege commands require an approval dialog. Secrets are redacted in logs.
- **Starter templates** — one-click new projects (Python CLI, FastAPI, PySide6,
  React, Express, static site, Animica agent, miner dashboard, wallet stub).
- **Animica ecosystem** — quick links to animica.org, the pool, wallet, chat, and
  train portals.

---

## Install

Use the project virtualenv (already has PySide6, httpx, pygments, markdown):

```bash
/root/animica/.venv/bin/python -m pip install -r requirements.txt
```

For a fresh environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

> Note: `GitService` shells out to the `git` binary via `subprocess` — GitPython
> is **not** required.

## Run

```bash
python main.py
```

This launches the dark, Animica-branded desktop window. Open a project from the
top toolbar, start a new chat, and begin pairing with the agent.

---

## Configure the Animica endpoint

Open **Settings** from the toolbar (or the right panel shortcut). You can set:

| Setting            | Default                          | Notes                                   |
|--------------------|----------------------------------|-----------------------------------------|
| Endpoint           | `https://pool.animica.org/v1`    | OpenAI-compatible base URL              |
| API key            | *(empty)*                        | Sent as `Authorization: Bearer …`       |
| Model              | `anm-fast-8b`                    | also `anm-pro-70b`                      |
| Provider kind      | `animica`                        | `animica` / `openai` / `ollama`         |
| Streaming          | on                               | token-by-token SSE                      |
| Temperature        | `0.3`                            | sampling                                |
| Max tokens         | `4096`                           | response cap                            |
| Request timeout    | `120s`                           |                                         |

Built-in endpoint presets:

- `https://pool.animica.org/v1` (Animica pool, distributed inference)
- `http://localhost:8000/v1` (local OpenAI-compatible server)
- `https://api.animica.org/v1` (hosted API)
- `http://localhost:11434/v1` (Ollama-compatible)

Any OpenAI-compatible endpoint works. The API key is stored locally and **never**
printed to logs (secret redaction is applied everywhere).

---

## Agent autonomy modes

The autonomy selector controls how much the agent may do on its own:

| Mode                | Tools? | Behaviour                                                        |
|---------------------|--------|-----------------------------------------------------------------|
| **Chat Only**       | no     | Pure conversation, no tools touch the project.                  |
| **Suggest Changes** | yes    | Proposes edits as diffs; you accept/reject each file.           |
| **Apply With Approval** | yes | Same, with explicit approval before applying.                  |
| **Full Project Agent**  | yes | Non-risky writes auto-apply; risky actions still need approval. |
| **Autonomous Loop**     | yes | Plans + executes multi-step tasks; risky actions still gated.   |

Under *Suggest* / *Apply With Approval*, file writes/patches/creates/renames
produce a **FileDiff** for the diff viewer. Under *Full Project Agent* /
*Autonomous Loop*, non-risky writes auto-apply — but `delete_file`,
`run_command`, `git_commit`, and `install_dependency` **always** require approval
(unless an "Always allow" approval has been recorded for the project/category).

---

## Safety & approval model

Animica Studio Qt is designed to never destroy files or deploy without your
consent.

- **Risky command categories** (always require approval): `destructive`
  (`rm -rf`, `del`, `mkfs`, `dd`, `> file`), `install` (`pip`/`npm`/`cargo`/`apt`…),
  `docker` (`docker`/`podman`/`kubectl`), `deploy` (`terraform apply`, `ssh`,
  publish/push to prod), `wallet` (keygen/seed/sign/transfer), `network`
  (`curl | bash`), `privilege` (`sudo`, `chmod 777`).
- **Approval dialog** — Approve once / Deny / **Always allow** (persisted per
  project + category).
- **Diff before apply** — accept/reject per file, with a rollback snapshot.
- **Sandboxing** — every file operation flows through path resolution that rejects
  absolute paths, `..` traversal, and symlinks leaving the project root.
- **Ignore globs** — `.git`, `node_modules`, `.venv`, `dist`, `build`, `target`,
  `__pycache__` (and friends) are skipped when walking/indexing.
- **Secret redaction** — API keys, tokens, passwords, and `key=value` secret
  pairs are masked in all logs via `config.redact(...)`.

---

## Project templates

From the **New Project** wizard you can scaffold a real, runnable starter project.
Templates live under `animica_studio/templates/<id>/` and are copied with
`{{name}}` / `{{slug}}` substitution by `TemplatesService`:

| Template          | What you get                                                 |
|-------------------|--------------------------------------------------------------|
| `python_cli`      | `main.py` (argparse) + `pyproject.toml`                       |
| `fastapi`         | `app/main.py` + `requirements.txt` + README                  |
| `qt_app`          | PySide6 hello window (`main.py`)                              |
| `react`           | `package.json` + `index.html` + `src/App.jsx` (Vite)         |
| `node_express`    | `package.json` + `server.js`                                 |
| `static_site`     | `index.html` + `style.css` + `app.js`                        |
| `animica_agent`   | tiny OpenAI-compatible Animica agent + README                |
| `miner_dashboard` | static dashboard hitting `pool.animica.org/api/mining/status`|
| `wallet_app`      | minimal read-only wallet / payment stub                      |

---

## Project layout

```
animica_studio/
  config.py            Settings, Paths, ProviderKind, redact()
  models.py            dataclasses + enums (Session, Message, ToolResult, …)
  db.py                SQLite Database (WAL, FK on)
  app.py               AnimicaStudioApp, main()
  assets/styles.qss    dark Animica theme
  services/            AnimicaClient, ProjectService, GitService,
                       CommandRunner, Indexer, TemplatesService
  agent/               ToolManager + ToolContext, AgentEngine, planner, context
  ui/                  MainWindow + panels (chat, editor, file tree, terminal,
                       settings, diff viewer, project wizard)
  templates/           starter projects (above)
main.py                entrypoint → animica_studio.app:main
tests/                 pytest (db, command classifier, sandbox, context, UI smoke)
```

---

## Development & tests

The test suite needs **no network and no display** (UI tests run offscreen):

```bash
QT_QPA_PLATFORM=offscreen /root/animica/.venv/bin/python -m pytest tests/ -q
```

Headless import sanity check:

```bash
QT_QPA_PLATFORM=offscreen /root/animica/.venv/bin/python -c \
  "import animica_studio.app, animica_studio.db, animica_studio.models, animica_studio.config"
```

---

## Roadmap

- **Miner-powered distributed inference** — route chat/agent calls across the
  Animica mining pool for cheap, scalable tokens.
- **Plugin / agent marketplace** — install community tools, agents, and templates.
- **Remote workspace sync** — open and edit projects on remote machines / sessions.
- **GitHub integration** — clone, PRs, issues, reviews, and CI from inside Studio.
- **One-click deploy** — ship templates to common hosts with guarded approval.
- **Wallet integration** — pay for inference / Pro from an Animica wallet.
- **Pro subscription** — higher limits, premium models, priority pool access.
- **Team collaboration** — shared sessions, projects, and approval policies.
- **Local model fallback** — seamless Ollama / local-server failover when offline.
- **Voice coding** — speak prompts and drive the agent hands-free.

---

## Ecosystem

[animica.org](https://animica.org) ·
[pool.animica.org](https://pool.animica.org) ·
[wallet.animica.org](https://wallet.animica.org) ·
[chat.animica.org](https://chat.animica.org) ·
[train.animica.org](https://train.animica.org)
