# Quick Fix: Genesis Hash Mismatch Error

## 🔴 Error You're Seeing

```
Code.GENESIS: genesis does not match pinned network genesis
[expected=0x..., found=0x..., genesis_path=/app/core/genesis/mainnet.json, ...]

ERROR: Application startup failed. Exiting.

Container status:
CONTAINER ID   IMAGE                  COMMAND                  STATUS
ad0b77d3a990   animica-mainnet-node   "/usr/bin/tini -g --…"   Restarting (3) 10 seconds ago
```

## 🔍 What This Means

Your Docker container has **outdated code** from before a genesis file update. The container was built with old hash constants that no longer match the current genesis file.

## ✅ Quick Fix (2 steps)

### Step 1: Rebuild Docker Images

```bash
# Stop the containers
docker compose -f ops/docker/docker-compose.mainnet.yml down

# Rebuild from scratch (no cache)
docker compose -f ops/docker/docker-compose.mainnet.yml build --no-cache
```

### Step 2: Clear Old Data (if needed)

If you're switching to a **new genesis** (chain reset), delete old blockchain data:

```bash
# Remove volumes (⚠️ This deletes all blockchain data!)
docker compose -f ops/docker/docker-compose.mainnet.yml down -v
```

Then restart:

```bash
docker compose -f ops/docker/docker-compose.mainnet.yml up -d
```

## ✔️ Verify It's Fixed

```bash
# Check container status (should say "Up")
docker compose -f ops/docker/docker-compose.mainnet.yml ps

# Check logs for success message
docker compose -f ops/docker/docker-compose.mainnet.yml logs node | grep "genesis"
```

You should see:
```
[genesis] Selected genesis: ... hash=0x8daaca93... pinned=0x8daaca93...
```

## 🎯 Prevention

**Always rebuild after pulling updates:**

```bash
git pull
docker compose build --no-cache  # ← Don't skip this!
docker compose up -d
```

## 📚 More Help

- **Detailed troubleshooting**: [ops/docker/TROUBLESHOOTING.md](ops/docker/TROUBLESHOOTING.md)
- **Genesis hash mismatch section**: See "Genesis Hash Mismatch" in the troubleshooting guide
- **Docker general info**: [ops/docker/README.md](ops/docker/README.md)

---

## Why Does This Happen?

When you build a Docker image, it copies the repository code (including `core/network_params.py` with the pinned genesis hash). When the genesis file is updated in the repository, your old Docker image still has the old hash constant, causing a mismatch.

**The fix is always**: Rebuild the Docker image to get the latest code.
