# Docker Troubleshooting Guide

This guide covers common issues when running Animica nodes with Docker and docker-compose.

---

## Genesis Hash Mismatch

### Symptoms

Node fails to start with an error like:

```
Code.GENESIS: genesis does not match pinned network genesis
[expected=0x6a16a931..., found=0x21b05e15..., genesis_path=/app/core/genesis/mainnet.json, chain_id=1, network=mainnet]

ERROR: Application startup failed. Exiting.
```

Container keeps restarting:
```
CONTAINER ID   IMAGE                  COMMAND                  STATUS
ad0b77d3a990   animica-mainnet-node   "/usr/bin/tini -g --…"   Restarting (3) 10 seconds ago
```

### Root Cause

Your Docker image contains **outdated code** with an old genesis hash constant. The genesis file hash validation is baked into the Docker image at build time in `core/network_params.py`.

When you pull repository updates (especially after a genesis reset or chain parameter changes), the **pinned genesis hash** changes, but your Docker image still has the old value.

### Solution

**1. Rebuild Your Docker Images**

Always rebuild images after pulling repository updates:

```bash
# For mainnet
docker compose -f ops/docker/docker-compose.mainnet.yml build --no-cache node

# For testnet
docker compose -f ops/docker/docker-compose.testnet.yml build --no-cache node

# For devnet
docker compose -f ops/docker/docker-compose.devnet.yml build --no-cache node
```

The `--no-cache` flag ensures a complete rebuild from scratch.

**2. Clear Old Chain Data (if needed)**

If you're switching to a new genesis (chain reset), you need to remove old blockchain data:

```bash
# Stop containers
docker compose -f ops/docker/docker-compose.mainnet.yml down

# Remove volumes (⚠️ DESTRUCTIVE - deletes all blockchain data)
docker volume rm animica_mainnet_chain_1_data

# Or use docker compose down with -v flag
docker compose -f ops/docker/docker-compose.mainnet.yml down -v

# Rebuild and restart
docker compose -f ops/docker/docker-compose.mainnet.yml build --no-cache
docker compose -f ops/docker/docker-compose.mainnet.yml up -d
```

**3. Verify the Fix**

```bash
# Check container status
docker compose -f ops/docker/docker-compose.mainnet.yml ps

# Check logs
docker compose -f ops/docker/docker-compose.mainnet.yml logs node

# Should see: "[genesis] Selected genesis: ... hash=0x8daaca93... pinned=0x8daaca93..."
```

### Prevention

After pulling updates:
1. **Always check git log** for mentions of "genesis" or "chain reset"
2. **Always rebuild Docker images** before restarting containers
3. If you see `CHAIN_RESET_TOUCHPOINT` comments in commits, expect a rebuild

---

## Permission Errors

### Symptoms

```
ERROR: Application startup failed. Exiting.
Likely cause: permission error while binding ports or accessing data.
```

Or:
```
OSError: [Errno 13] Permission denied: '/data/chain-1'
```

### Root Cause

The container runs as a non-root user (`animica` with UID 10001), but the data directory has incorrect permissions.

### Solution

**For Named Volumes (default):**

Docker manages permissions automatically. No action needed.

**For Bind Mounts:**

If you're using bind mounts (e.g., `-v ./data:/data`), fix permissions:

```bash
# For mainnet node (UID 10001)
sudo chown -R 10001:10001 ./data

# Or for dev environments, you can use your own UID
# (requires modifying docker-compose to pass --user flag)
```

**Verify:**

```bash
ls -la ./data
# Should show: drwxr-xr-x ... 10001 10001 ... chain-1
```

---

## Port Binding Errors

### Symptoms

```
Error starting userland proxy: listen tcp4 0.0.0.0:8545: bind: address already in use
```

### Root Cause

Another process is using the port.

### Solution

**1. Find what's using the port:**

```bash
lsof -i :8545
# Or on some systems:
netstat -tulpn | grep 8545
```

**2. Stop the conflicting service:**

```bash
# If it's another Animica node:
docker compose down

# If it's a system service:
sudo systemctl stop <service-name>

# If it's a stray process:
kill <PID>
```

**3. Or change the port:**

In your `.env` file or docker-compose override:
```bash
HOST_RPC_PORT=8546  # Use different port
```

Then:
```bash
docker compose up -d
```

---

## Image Not Found

### Symptoms

```
ERROR: pull access denied for animica-mainnet-node, repository does not exist
```

### Root Cause

The image hasn't been built locally, and no pre-built image exists in a registry.

### Solution

Build the image:

```bash
docker compose -f ops/docker/docker-compose.mainnet.yml build
docker compose -f ops/docker/docker-compose.mainnet.yml up -d
```

---

## Container Exits Immediately

### Symptoms

Container status shows:
```
CONTAINER ID   IMAGE                  STATUS
ad0b77d3a990   animica-mainnet-node   Exited (1) 2 seconds ago
```

### Root Cause

Usually a configuration error or missing dependency.

### Solution

**1. Check logs:**

```bash
docker compose -f ops/docker/docker-compose.mainnet.yml logs node
```

**2. Common issues:**

- **Genesis hash mismatch**: See section above
- **Missing environment variables**: Check `.env` file
- **Database corruption**: Remove volumes and restart
- **Dependency missing**: Rebuild image

**3. Interactive debugging:**

```bash
# Start container with shell
docker compose -f ops/docker/docker-compose.mainnet.yml run --rm --entrypoint /bin/bash node

# Inside container, try starting manually:
python -m rpc
```

---

## Cannot Connect to RPC

### Symptoms

```bash
curl http://localhost:8545/healthz
curl: (7) Failed to connect to localhost port 8545: Connection refused
```

### Root Cause

Either the container isn't running, or port mapping is incorrect.

### Solution

**1. Verify container is running:**

```bash
docker compose ps
# Should show "Up" status
```

**2. Check port mapping:**

```bash
docker compose ps
# Should show "0.0.0.0:8545->8545/tcp"
```

**3. Check if service is listening inside container:**

```bash
docker compose exec node curl -f http://127.0.0.1:8545/healthz
```

If this works but external doesn't, check firewall rules.

---

## Disk Space Issues

### Symptoms

```
Error: No space left on device
```

Or container performance degrades over time.

### Root Cause

Docker volumes filling up disk space.

### Solution

**1. Check disk usage:**

```bash
df -h
docker system df
```

**2. Clean up unused data:**

```bash
# Remove stopped containers
docker container prune

# Remove unused images
docker image prune -a

# Remove unused volumes (⚠️ CAREFUL)
docker volume prune

# Clean everything (⚠️ DESTRUCTIVE)
docker system prune -a --volumes
```

**3. For production, monitor disk usage:**

Set up alerts when disk usage exceeds 80%.

---

## Network Connectivity Issues

### Symptoms

Container can't connect to internet or other containers.

### Root Cause

Docker network misconfiguration or firewall rules.

### Solution

**1. Check Docker network:**

```bash
docker network ls
docker network inspect animica-mainnet_mainnet
```

**2. Test connectivity:**

```bash
# From container
docker compose exec node curl -f https://google.com

# Between containers
docker compose exec node curl -f http://services:8787/healthz
```

**3. Recreate network:**

```bash
docker compose down
docker network prune
docker compose up -d
```

---

## Need More Help?

1. **Check logs in detail:**
   ```bash
   docker compose logs --tail=100 node
   docker compose logs -f  # Follow in real-time
   ```

2. **Enable debug logging:**
   In `.env`:
   ```bash
   ANIMICA_LOG_LEVEL=DEBUG
   ```

3. **Join community channels** or open a GitHub issue with:
   - Docker version: `docker --version`
   - Compose version: `docker compose version`
   - OS and architecture
   - Full error logs
   - Output of `docker compose ps` and `docker compose logs`
