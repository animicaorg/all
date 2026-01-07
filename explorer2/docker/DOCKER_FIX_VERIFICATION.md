# Explorer2 Docker Configuration Fix - Verification Guide

## Problem Summary

The explorer2 component was experiencing a **502 Bad Gateway** error from nginx when deployed using Docker. This was caused by:

1. **Port Mismatch**: The Dockerfile.api was exposing port 3001 but docker-compose was configuring port 8081
2. **Missing RPC Configuration**: No RPC URL was configured for Docker deployments, causing connection failures

## Changes Made

### 1. Fixed Port Configuration
- **File**: `explorer2/docker/Dockerfile.api`
- **Change**: Updated `EXPOSE 3001` to `EXPOSE 8081`
- **Impact**: API server now correctly exposes port 8081 to match docker-compose configuration

### 2. Added RPC URL Configuration
- **File**: `explorer2/docker/docker-compose.explorer2.yml`
- **Change**: Added `EXPLORER2_RPC_URL` environment variable with default value `http://host.docker.internal:8545/rpc`
- **Change**: Added `extra_hosts` configuration to enable `host.docker.internal` DNS resolution
- **Impact**: API can now connect to RPC nodes running on the host machine

### 3. Updated Documentation
- **File**: `explorer2/README.md`
- **Change**: Added Docker deployment instructions with RPC URL configuration examples
- **Impact**: Users now have clear guidance on how to deploy with custom RPC endpoints

## Verification Steps

### 1. Verify Configuration Files

Check that the files have the correct values:

```bash
# Check Dockerfile.api has correct port
grep "EXPOSE" explorer2/docker/Dockerfile.api
# Expected output: EXPOSE 8081

# Check docker-compose has RPC URL configured
grep "EXPLORER2_RPC_URL:" explorer2/docker/docker-compose.explorer2.yml | grep -v "^#"
# Expected output: EXPLORER2_RPC_URL: ${EXPLORER2_RPC_URL:-http://host.docker.internal:8545/rpc}

# Check extra_hosts is present
grep -A 1 "extra_hosts" explorer2/docker/docker-compose.explorer2.yml
# Expected output should include: - "host.docker.internal:host-gateway"
```

### 2. Test Docker Compose Configuration

Validate the docker-compose configuration:

```bash
cd /path/to/animica/all
docker compose -f explorer2/docker/docker-compose.explorer2.yml config --quiet
# Should succeed with no errors
```

### 3. Test Build Process

Build the Docker images:

```bash
# Build API image
docker compose -f explorer2/docker/docker-compose.explorer2.yml build explorer2-api

# Build Web image
docker compose -f explorer2/docker/docker-compose.explorer2.yml build explorer2-web
```

### 4. Test with Default Configuration

Deploy with default settings (requires RPC node on host at port 8545):

```bash
# Start the services
docker compose -f explorer2/docker/docker-compose.explorer2.yml up -d

# Wait for services to become healthy (may take 10-20 seconds)
docker compose -f explorer2/docker/docker-compose.explorer2.yml ps

# Check API health endpoint
curl http://localhost:8081/api/health
# Expected: {"ok":true,"timestamp":"..."}

# Check web UI is accessible
curl -I http://localhost:3001
# Expected: HTTP/1.1 200 OK

# Check logs for errors
docker compose -f explorer2/docker/docker-compose.explorer2.yml logs explorer2-api
docker compose -f explorer2/docker/docker-compose.explorer2.yml logs explorer2-web

# Clean up
docker compose -f explorer2/docker/docker-compose.explorer2.yml down
```

### 5. Test with Custom RPC URL

Deploy with a custom RPC endpoint:

```bash
# Set custom RPC URL
export EXPLORER2_RPC_URL=http://your-rpc-node:8545/rpc

# Or inline:
EXPLORER2_RPC_URL=http://your-rpc-node:8545/rpc docker compose -f explorer2/docker/docker-compose.explorer2.yml up -d

# Verify API can reach RPC node
docker compose -f explorer2/docker/docker-compose.explorer2.yml logs explorer2-api | grep "RPC"
# Should show successful RPC connection logs

# Clean up
docker compose -f explorer2/docker/docker-compose.explorer2.yml down
```

### 6. Access the Explorer

Once deployed:

- **Web UI**: http://localhost:3001
- **API**: http://localhost:8081
- **API Health Check**: http://localhost:8081/api/health
- **API Diagnostics**: http://localhost:8081/api/diagnostics

The diagnostics endpoint shows:
- Connection mode (RPC or Local DB)
- RPC URL being used
- Chain ID
- Current head height
- Database status

### 7. Troubleshooting

If you still see 502 errors:

1. **Check API is running**:
   ```bash
   docker compose -f explorer2/docker/docker-compose.explorer2.yml ps
   # explorer2-api should show "healthy" status
   ```

2. **Check API logs**:
   ```bash
   docker compose -f explorer2/docker/docker-compose.explorer2.yml logs explorer2-api
   # Look for connection errors or startup failures
   ```

3. **Verify RPC node is accessible**:
   ```bash
   # From host machine
   curl -X POST http://127.0.0.1:8545/rpc -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'
   
   # From within API container (test RPC connectivity)
   docker compose -f explorer2/docker/docker-compose.explorer2.yml exec explorer2-api \
     sh -c 'node -e "
       fetch(\"http://host.docker.internal:8545/rpc\", {
         method: \"POST\",
         headers: { \"Content-Type\": \"application/json\" },
         body: JSON.stringify({
           jsonrpc: \"2.0\",
           id: 1,
           method: \"chain.getChainId\",
           params: []
         })
       })
       .then(r => r.json())
       .then(console.log)
       .catch(console.error)
     "'
   ```

4. **Check nginx proxy configuration**:
   ```bash
   docker compose -f explorer2/docker/docker-compose.explorer2.yml exec explorer2-web cat /etc/nginx/conf.d/default.conf
   # Verify proxy_pass points to http://explorer2-api:8081/api/
   ```

5. **Test API directly** (bypass nginx):
   ```bash
   curl http://localhost:8081/api/health
   # If this works but web UI shows 502, the issue is with nginx proxy
   ```

## Expected Results

After these fixes:

✅ **API Container**: Listens on port 8081 (matches docker-compose configuration)
✅ **RPC Connection**: Successfully connects to RPC node via `host.docker.internal` or custom URL
✅ **Web Container**: Nginx successfully proxies `/api/*` requests to API container
✅ **No 502 Errors**: All API requests succeed when RPC node is available
✅ **Proper Fallback**: If RPC is unavailable, API falls back to local database (if available) or shows clear error message

## Technical Details

### Port Configuration
- **API Port**: 8081 (configured via `EXPLORER2_PORT` environment variable)
- **Web Port**: 80 (nginx default, mapped to host port 3001)
- **RPC Port**: 8545 (on host machine, accessed via `host.docker.internal`)

### Network Configuration
- Both services use Docker Compose default network
- Services communicate via service names (e.g., `explorer2-api`)
- API can reach host services via `host.docker.internal` (configured with `extra_hosts`)

### Health Checks
- **API Health Check**: Calls `/api/health` endpoint every 10 seconds
- **Web Health Check**: Uses `wget` to verify nginx is serving content
- **Dependencies**: Web service waits for API to become healthy before starting

## Testing Checklist

- [ ] Docker Compose configuration validates without errors
- [ ] API Docker image builds successfully
- [ ] Web Docker image builds successfully
- [ ] API container starts and passes health check
- [ ] Web container starts and passes health check
- [ ] Web container waits for API to be healthy
- [ ] API successfully connects to RPC node
- [ ] Web UI is accessible at http://localhost:3001
- [ ] API is accessible at http://localhost:8081
- [ ] API `/api/health` endpoint returns success
- [ ] API `/api/diagnostics` endpoint shows correct RPC URL
- [ ] Web UI can fetch data from API (no 502 errors)
- [ ] nginx logs show successful proxy requests
- [ ] API logs show successful RPC connections

## Related Files

- `explorer2/docker/Dockerfile.api` - API Docker image definition
- `explorer2/docker/Dockerfile.web` - Web Docker image definition
- `explorer2/docker/docker-compose.explorer2.yml` - Docker Compose configuration
- `explorer2/docker/nginx.conf` - nginx reverse proxy configuration
- `explorer2/api/src/config.ts` - API configuration (reads environment variables)
- `explorer2/README.md` - Updated with Docker deployment instructions
