# Prometheus Configuration for Animica

This directory contains Prometheus configuration files for monitoring Animica nodes.

## Files

- `prometheus.multinode.yml` - Configuration for the multi-node test network (3 nodes)

## Usage with Docker Compose

The prometheus configuration is automatically used when running the multi-node setup:

```bash
docker-compose -f docker-compose.multinode.yml up -d
```

Access Prometheus at: http://localhost:9090

## Metrics Endpoints

Each Animica node exposes metrics on port 9000:

- Node 1: http://node1:9000/metrics
- Node 2: http://node2:9000/metrics
- Node 3: http://node3:9000/metrics

## Available Metrics

### P2P Metrics

- `p2p_peers{state="connected"}` - Number of connected peers
- `p2p_msgs_total{topic="blocks"}` - Total messages by topic
- `p2p_bytes_total{dir="rx"}` - Total bytes transferred
- `p2p_rtt_seconds` - Peer round-trip time
- `p2p_rejects_total{reason}` - Message rejections by reason

### Chain Metrics

- `chain_head_height` - Current blockchain height
- `chain_sync_status` - Sync status (0=syncing, 1=synced)
- `chain_blocks_total` - Total blocks imported
- `chain_txs_total` - Total transactions processed

### Mempool Metrics

- `mempool_size` - Number of transactions in mempool
- `mempool_bytes` - Total bytes in mempool

## Example Queries

### Average Peer Count
```promql
avg(p2p_peers{state="connected"})
```

### Network Throughput
```promql
rate(p2p_bytes_total{dir="rx"}[1m])
```

### Block Production Rate
```promql
rate(chain_blocks_total[5m])
```

## Grafana Dashboards

Pre-configured Grafana dashboards are available in `/ops/grafana/dashboards/`.

Access Grafana at: http://localhost:3001 (admin/animica)
