# Devnet spinup: node1 container exits immediately with code 0, causing spin_all.sh to fail

## Summary
When running the Animica devnet spin script, all images build successfully (node1, node2, miner, services), but the `node1` container immediately exits with code 0. Because node1 is a dependency for the rest of the stack, docker compose fails and the devnet never comes up.

## Environment
- Host: Ubuntu 24.04 (root on a VPS)
- Repo: https://github.com/animicaorg/all
- Branch: main
- Directory: /root/animica
- Command used to start devnet:

```bash
cd /root/animica
./ops/spinup/spin_all.sh
```

## Expected behavior
- `./ops/spinup/spin_all.sh` builds the devnet images and brings up:
  - `animica-node1` (primary node)
  - `animica-node2`
  - `animica-miner`
  - `animica-studio-services`
  - `animica-explorer`
- `node1` stays running as a long-lived node process (listening on the configured RPC / P2P ports), so the miner, explorer, and services all remain healthy.

## Actual behavior
1. Images build fine, including `animica-devnet-node1`:

```
animica-devnet-node1  Built
animica-devnet-node2  Built
animica-devnet-miner  Built
animica-devnet-services  Built
```

2. Then when docker compose attaches:

```
Attaching to animica-explorer, animica-miner, animica-node1, animica-node2, animica-studio-services
animica-node1 exited with code 0
dependency failed to start: container animica-node1 exited (0)
[2025-11-28T23:41:59Z] docker compose failed with exit code 1.
```

3. The spinup script prints the tip:

```
Tip: if a dependency (e.g., node1) exited early, inspect its logs with:
  docker compose -f /root/animica/tests/devnet/docker-compose.yml --profile dev logs --tail=200 <service>
```

So the core issue is: `animica-node1` exits cleanly and immediately after start, instead of running as a daemon / server.

## Relevant docker compose output
(abridged, but this is the pattern every time):

```
...
 animica-devnet-node2  Built
 animica-devnet-miner  Built
 animica-devnet-services  Built
 animica-devnet-node1  Built
 Container animica-node1  Recreate
 Container animica-node1  Recreated
 Container animica-studio-services  Recreate
 Container animica-miner  Recreate
 Container animica-node2  Recreate
 Container animica-miner  Recreated
 Container animica-studio-services  Recreated
 Container animica-node2  Recreated
Attaching to animica-explorer, animica-miner, animica-node1, animica-node2, animica-studio-services
animica-node1 exited with code 0
dependency failed to start: container animica-node1 exited (0)
[2025-11-28T23:41:59Z] docker compose failed with exit code 1.
...
NAME            IMAGE                  COMMAND                  SERVICE   CREATED          STATUS                              PORTS
animica-node1   animica-devnet-node1   "/usr/bin/tini -g --…"   node1     14 seconds ago   Exited (0) Less than a second ago
```

Earlier runs also showed warnings like these (before the last changes to `docker-compose.yml`), which may or may not still be relevant:

```
time="2025-11-29T00:31:00+01:00" level=warning msg="The \"GENESIS_PATH\" variable is not set. Defaulting to a blank string."
time="2025-11-29T00:31:00+01:00" level=warning msg="The \"DB_URL\" variable is not set. Defaulting to a blank string."
time="2025-11-29T00:31:00+01:00" level=warning msg="The \"RPC_HOST\" variable is not set. Defaulting to a blank string."
time="2025-11-29T00:31:00+01:00" level=warning msg="The \"CHAIN_ID\" variable is not set. Defaulting to a blank string."
...
```

After the latest git pull those env warnings appear to have been reduced, but `node1` still exits immediately.

## What I suspect / what would help
From the outside, it looks like:
- The `node1` container’s CMD / entrypoint is either:
  - Running a one-shot init (e.g., genesis generation or some CLI command) and then exiting cleanly, or
  - Printing a help/usage message due to missing args/env and then exiting with status 0.

## What is needed
1. Make sure the `node1` container runs a long-lived node process in devnet mode by default, e.g. something like:

```yaml
# tests/devnet/docker-compose.yml (pseudo)
services:
  node1:
    image: animica-devnet-node1
    command: ["python", "-m", "animica.node", "--config", "/config/devnet-node1.yaml"]
    # or whatever the correct node entrypoint is
```

2. Confirm and document the expected environment variables for devnet (`GENESIS_PATH`, `DB_URL`, `CHAIN_ID`, `RPC_HOST`, `RPC_PORT`, etc.), and ensure they’re properly set via:
   - `tests/devnet/.env`
   - or inline environment in `tests/devnet/docker-compose.yml`
   - and wired into the node startup command.

3. Optionally, update `./ops/spinup/spin_all.sh` to automatically run:

```bash
docker compose -f tests/devnet/docker-compose.yml --profile dev logs --tail=200 node1
```

when `node1` exits early, so devs immediately see the underlying node error instead of just “exited with code 0”.

Once `node1` is wired to actually start the devnet node and keep running, `./ops/spinup/spin_all.sh` should complete successfully and expose the RPC / explorer endpoints as described in the docs.
