# Rich List Feature

## Overview

The Rich List feature displays addresses ranked by their ANM balance in the Explorer2 UI. It provides transparency into token distribution and helps users understand wealth concentration on the Animica blockchain.

## Architecture

### Components

1. **RPC Layer (Python)**
   - `state.getRichList(limit, offset)` - Returns paginated list of addresses sorted by balance
   - `state.getTotalSupply()` - Returns total supply and address count

2. **API Layer (TypeScript)**
   - `GET /api/richlist?limit=100&offset=0` - Rich list with pagination
   - `GET /api/richlist/summary` - Summary statistics (total supply, concentration metrics)

3. **UI Layer (React)**
   - `/richlist` page with table, pagination, and summary cards
   - Displays rank, address, balance, and % of supply

## How Rich List is Computed

### Data Source

The Rich List uses the canonical chain state from `StateDB.iter_accounts()`, which iterates over all accounts stored in the state database at the current indexed height.

### Computation Steps

1. **Account Enumeration**: Iterate over all accounts using `StateDB.iter_accounts()`
2. **Filtering**: Exclude accounts with zero balance
3. **Sorting**: Sort accounts by balance in descending order
4. **Ranking**: Assign 1-indexed rank based on sorted position
5. **Pagination**: Apply offset and limit for efficient data transfer
6. **Formatting**: Convert addresses to bech32 format (`anim1...`)

### Balance Accuracy

- **Source of Truth**: Balances match `state.getBalance(address)` RPC calls
- **Consistency**: Data is read from the same state DB snapshot at a specific height
- **Determinism**: Results are reproducible at the same chain height
- **Units**: Balances are stored in nANM (10^-9 ANM) and displayed as ANM

### Supply Calculation

Total supply is computed by summing all non-zero account balances from the state DB. This represents the actual circulating supply at the indexed height.

### Concentration Metrics

The summary page shows:
- **Top 10 Hold**: % of supply held by top 10 addresses
- **Top 100 Hold**: % of supply held by top 100 addresses
- **Top 1000 Hold**: % of supply held by top 1000 addresses (if available)

These metrics help understand wealth concentration and distribution.

## Performance Considerations

### Scaling to Millions of Addresses

1. **Caching**: Results are cached using request coalescer to prevent duplicate queries
2. **Pagination**: Server-side pagination reduces memory footprint and network transfer
3. **Indexing**: StateDB uses efficient prefix scans over account keys
4. **Computation Cost**: Full scan is O(n) where n = account count, but only done once per request due to caching

### Performance Targets

- **Query Time**: < 500ms for top 100 on a reasonable dev machine
- **Memory**: O(n) for full account list, but paginated results use O(limit)
- **Network**: Only transfer requested page (typically 100 items = ~5KB)

## Known Limitations

### 1. RPC Mode Dependency

The Rich List feature requires the node to support:
- `state.getRichList` RPC method
- `state.getTotalSupply` RPC method

If these methods are not available, the API returns 501 Not Implemented.

### 2. No Local DB Fallback (Yet)

Currently, the feature only works in RPC mode. Local DB mode fallback is not implemented because:
- Would require duplicating account iteration logic in TypeScript
- SQLite operations in Node.js are slower for full scans
- Most deployments use RPC mode anyway

Future enhancement: Add local DB fallback using `better-sqlite3` to scan PFX_ACC keys.

### 3. Snapshot Consistency

The rich list reflects the state at the **indexed height**, which may lag behind the chain head if:
- The node is syncing
- There's a delay in state processing

The UI displays the indexed height to make this clear.

### 4. Reorg Handling

If the chain supports reorgs:
- The rich list automatically updates when the state DB updates
- No special rollback logic needed (relies on node's state DB being canonical)
- For finalized checkpoints, query at finalized height (not implemented yet)

### 5. Performance on Large Chains

For chains with millions of addresses:
- First load may take 1-5 seconds (full scan)
- Subsequent loads are faster due to caching
- Consider adding a background job to pre-compute and cache rich list snapshots at block intervals

## Security & Privacy

- **Public Data Only**: Only displays public on-chain balances
- **No Deanonymization**: Does not attempt to link addresses to identities
- **No Private Keys**: Never accesses or exposes private keys
- **Rate Limiting**: API should implement rate limiting to prevent abuse

## Testing

### Manual Testing

1. Start a local node with some funded addresses
2. Start Explorer2 API: `cd explorer2/api && pnpm start`
3. Navigate to `http://localhost:8081/api/richlist`
4. Verify:
   - Addresses are sorted by balance (highest first)
   - Total supply matches sum of balances
   - Pagination works (offset parameter)

### Automated Testing

Run RPC method tests:
```bash
cd /home/runner/work/all/all
pytest rpc/tests/test_rich_list_rpc.py -v
```

### Verification Script

Use the verification script to cross-check balances:
```bash
cd explorer2/api
node scripts/verify_richlist.js --sample 10
```

This script:
1. Fetches top N addresses from rich list API
2. Queries node RPC for each address's balance
3. Compares and reports any mismatches

## API Reference

### GET /api/richlist

Returns paginated rich list.

**Query Parameters:**
- `limit` (optional, default: 100, max: 1000) - Number of addresses to return
- `offset` (optional, default: 0) - Number of addresses to skip

**Response:**
```json
{
  "height": 12345,
  "totalAddresses": 567,
  "items": [
    {
      "rank": 1,
      "address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4",
      "balance": "0x3b9aca00",
      "pctSupply": 5.42
    }
  ],
  "nextOffset": 100
}
```

### GET /api/richlist/summary

Returns rich list summary statistics.

**Response:**
```json
{
  "height": 12345,
  "totalSupply": "0x3b9aca00",
  "addressCount": 567,
  "top10Pct": 42.5,
  "top100Pct": 78.3,
  "top1000Pct": 95.1
}
```

## Future Enhancements

1. **Caching Layer**: Add Redis/DB cache for pre-computed rich lists at finalized heights
2. **Historical Data**: Track rich list changes over time
3. **Charts**: Visualize wealth distribution (Gini coefficient, Lorenz curve)
4. **Filters**: Filter by address type, balance range, etc.
5. **Export**: CSV/JSON export for analysis
6. **Websocket Updates**: Real-time updates when balances change
7. **Local DB Support**: Implement fallback for local DB mode
8. **Background Jobs**: Pre-compute and cache rich list snapshots periodically

## Maintenance

### Monitoring

Watch for:
- Query times > 1 second
- High memory usage during full scans
- RPC timeouts on large chains
- Cache hit rate

### Debugging

Check logs for:
- `state.getRichList` call times
- Total addresses scanned
- Cache hits/misses
- Error messages from state DB iteration

### Tuning

Adjust:
- Cache TTL based on block time
- Pagination limits based on performance
- Memory limits for account iteration
- Rate limits for API endpoints
