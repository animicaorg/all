# Explorer Troubleshooting Guide

This guide helps you diagnose and fix common issues with the Animica Blockchain Explorer.

## Table of Contents
- [Connection Issues](#connection-issues)
- [Configuration Issues](#configuration-issues)
- [Browser Console Debugging](#browser-console-debugging)
- [Network Issues](#network-issues)
- [Common Error Messages](#common-error-messages)

---

## Connection Issues

### "Unable to fetch blockchain data" Error

**Symptom**: The explorer displays "Unable to fetch blockchain data. Please ensure the RPC node is running and accessible at the configured URL."

**Possible Causes & Solutions**:

#### 1. RPC Node Not Running
- **Check**: Verify the RPC node is running and accessible
- **Test**: Use `curl` to test the endpoint:
  ```bash
  curl -X POST https://your-rpc-url/rpc \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'
  ```
- **Expected Response**: Should return a JSON-RPC response with `result` field
- **Fix**: Start your RPC node or use a different endpoint

#### 2. Incorrect RPC URL Configuration
- **Check**: Verify your `.env` file has the correct values:
  ```env
  VITE_RPC_URL=https://rpc.animica.org/rpc
  VITE_CHAIN_ID=1
  ```
- **For mainnet**: Use `https://rpc.animica.org/rpc`
- **For testnet**: Use your testnet RPC URL
- **For local development**: Use `http://localhost:8545` or `http://127.0.0.1:8545`
- **Fix**: Update the configuration and restart the dev server

#### 3. CORS (Cross-Origin Resource Sharing) Issues
- **Symptom**: Browser console shows CORS errors
- **Check**: Open browser DevTools (F12) → Console tab
- **Look for**: Error messages containing "CORS", "Access-Control-Allow-Origin", or "blocked by CORS policy"
- **Fix**: Configure your RPC server to allow the explorer's origin:
  ```python
  # In your RPC server configuration
  CORS_ORIGINS = [
      "http://localhost:5173",  # Vite dev server
      "https://explorer.animica.org",  # Production
      # Add your custom domains
  ]
  ```

#### 4. Firewall or Network Restrictions
- **Check**: Ensure the RPC port (typically 8545) is accessible
- **Test**: Try accessing the RPC URL from your browser directly
- **Fix**: Configure firewall rules to allow traffic to the RPC port
  ```bash
  # Example for UFW (Ubuntu)
  sudo ufw allow 8545/tcp
  ```

#### 5. SSL/TLS Certificate Issues (HTTPS)
- **Symptom**: Mixed content warnings or certificate errors
- **Check**: Ensure both the explorer and RPC use HTTPS (or both HTTP in dev)
- **Fix**: Use matching protocols (HTTPS→HTTPS or HTTP→HTTP) or configure proper SSL certificates

---

## Configuration Issues

### Environment Variables Not Loading

**Symptom**: Explorer shows default values instead of your configuration

**Solution**:
1. Ensure the file is named exactly `.env` (not `.env.txt`)
2. Restart the development server after changing environment variables
3. Clear browser cache and reload
4. Verify the variables are prefixed with `VITE_` (required for Vite)
5. `.env.local` overrides are unsupported; rename them to `.env`

### Chain ID Mismatch

**Symptom**: Explorer connects but displays warning about chain ID mismatch

**Solution**:
1. Check the chain ID returned by your RPC node:
   ```bash
   curl -X POST http://your-rpc-url \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'
   ```
2. Update `VITE_CHAIN_ID` in your `.env` to match (legacy values like `659658` / `0xa11ca`
   are now normalized to `1`, but you should still update the config to avoid warnings)
3. Restart the dev server

### WebSocket Connection Failures

**Symptom**: Explorer shows data but doesn't update in real-time

**Solution**:
1. Check WebSocket URL configuration:
   ```env
   VITE_RPC_WS=wss://rpc.animica.org/ws
   # or for local:
   VITE_RPC_WS=ws://localhost:8546
   ```
2. Ensure the WebSocket endpoint is accessible
3. Test WebSocket connection:
   ```bash
   wscat -c ws://localhost:8546
   # or use a browser WebSocket tester
   ```
4. Check firewall rules for WebSocket port (typically 8546)

---

## Browser Console Debugging

### Enabling Debug Logging

The explorer includes detailed logging to help diagnose issues. Open browser DevTools (F12) and check the Console tab.

**Log Levels**:
- `[RPC] getChainId:` - Chain ID retrieval
- `[RPC] getHead:` - Latest block head fetching
- `[RPC] getBlock:` - Individual block fetching
- `[ws] connected` - WebSocket connection established
- `[ws] closed` - WebSocket connection closed

**Common Log Messages**:

1. **"[RPC] chain.getChainId failed, trying eth_chainId fallback"**
   - Normal fallback behavior, explorer is trying multiple RPC methods
   - If both fail, check RPC node is running

2. **"[RPC] Both getBlockByHeight and getBlockByNumber failed"**
   - Block doesn't exist or RPC error
   - Check block height exists on the chain

3. **"[ws] error event"** or **"[ws] closed"**
   - WebSocket connection issue
   - Check VITE_RPC_WS configuration and network connectivity

4. **"[RPC] Failed to establish WebSocket connection"**
   - WebSocket URL incorrect or service not running
   - Verify WS endpoint and protocol (ws:// vs wss://)

### Browser Network Tab

1. Open DevTools → Network tab
2. Look for requests to your RPC URL
3. Check:
   - **Status codes**: Should be 200 for successful requests
   - **Response**: Should contain valid JSON-RPC responses
   - **Timing**: Long delays indicate network/server issues
   - **Headers**: Check CORS headers are present

---

## Network Issues

### Slow or Timeout Errors

**Solutions**:
1. Increase timeout values (default is 10 seconds):
   - Edit `src/services/rpc.ts` if needed
   - Check your RPC server performance
2. Use a closer/faster RPC endpoint
3. Check network latency:
   ```bash
   ping your-rpc-host
   traceroute your-rpc-host
   ```

### Intermittent Connection Drops

**Solutions**:
1. Check RPC server logs for errors
2. Verify server has sufficient resources (CPU, memory)
3. Check for rate limiting on the RPC server
4. Enable auto-reconnect (enabled by default for WebSocket)

---

## Common Error Messages

### "HTTP 404" or "Method not found"

**Cause**: RPC method not supported by your node

**Solution**: 
- Update your RPC node to support required methods:
  - `chain.getChainId` (or `eth_chainId`)
  - `chain.getHead`
  - `chain.getBlockByHeight` (or `chain.getBlockByNumber`)
- Check RPC server implementation matches expected methods

### "HTTP 429 - Too Many Requests"

**Cause**: Rate limiting on RPC server

**Solution**:
- Reduce polling frequency
- Implement caching
- Use a dedicated RPC endpoint
- Contact RPC provider to increase limits

### "HTTP 500 - Internal Server Error"

**Cause**: RPC server error

**Solution**:
- Check RPC server logs
- Verify blockchain data integrity
- Restart RPC server if necessary
- Report bug to RPC server maintainers

### "TypeError: Failed to fetch" or "Network Error"

**Cause**: DNS resolution failure, network connectivity issue, or CORS

**Solution**:
- Check DNS resolution: `nslookup your-rpc-host`
- Verify network connectivity
- Check CORS configuration
- Try accessing from different network

---

## Testing Your Configuration

### Quick Health Check

Run this command to test your RPC endpoint:

```bash
# Test HTTP endpoint
curl -X POST $VITE_RPC_URL \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'

# Expected output: JSON with height, hash, etc.
```

### Full Connection Test

1. **Test HTTP RPC**:
   ```bash
   curl -X POST http://localhost:8545 \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'
   ```

2. **Test WebSocket** (requires `wscat`):
   ```bash
   npm install -g wscat
   wscat -c ws://localhost:8546
   # Then send:
   {"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}
   ```

3. **Browser Test**:
   - Open browser console
   - Navigate to `http://localhost:5173` (or your explorer URL)
   - Check Console tab for any errors
   - Check Network tab for RPC requests

---

## Getting Help

If you're still experiencing issues:

1. **Collect Information**:
   - Browser console logs (DevTools → Console)
   - Network tab data (DevTools → Network)
   - RPC server logs
   - Configuration files (`.env`, `vite.config.ts`)

2. **Check Documentation**:
   - [README.md](../README.md) - Setup and quickstart
   - [ARCHITECTURE.md](./ARCHITECTURE.md) - Technical details
   - [DEPLOYMENT.md](./DEPLOYMENT.md) - Deployment guide

3. **Report Issues**:
   - GitHub Issues: Include error messages, logs, and configuration
   - Community Forums: Ask questions with context
   - Discord/Telegram: Real-time support

---

## Advanced Debugging

### Enable Verbose Logging

Edit `src/services/rpc.ts` and change `console.debug` to `console.log` to see all debug messages.

### Inspect Raw Responses

Add temporary logging to see raw RPC responses:

```typescript
// In src/services/rpc.ts
const result = await this.call<any>('chain.getHead');
console.log('Raw getHead response:', JSON.stringify(result, null, 2));
```

### Network Analysis

Use browser DevTools → Network tab:
- Right-click a request → Copy as cURL
- Replay the request in terminal
- Inspect headers and response body

### WebSocket Debugging

Use browser DevTools → Network → WS tab to monitor WebSocket frames:
- See all messages sent/received
- Check connection status
- Monitor reconnection attempts
