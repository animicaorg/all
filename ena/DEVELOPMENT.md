# ENA Developer Guide

This guide is for developers who want to run, modify, or contribute to the ENA service.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Animica CLI                             │
│                   (User Interface)                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    ENA FastAPI Server                        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   Payment   │  │    Model     │  │     Rate     │       │
│  │     Gate    │  │   Registry   │  │   Limiter    │       │
│  └─────────────┘  └──────────────┘  └──────────────┘       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │  Inference  │  │   Database   │  │     RPC      │       │
│  │   Engine    │  │  (SQLite)    │  │    Client    │       │
│  └─────────────┘  └──────────────┘  └──────────────┘       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Animica Blockchain RPC                          │
│           (Transaction Verification)                         │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
ena/
├── animica/                  # Animica blockchain integration
│   ├── __init__.py
│   ├── address.py           # Address validation
│   ├── animica_rpc.py       # RPC client with circuit breaker
│   ├── tx_builder.py        # Transaction building
│   └── verify.py            # Payment verification
│
├── services/
│   └── ena_node/            # Main FastAPI service
│       ├── __init__.py
│       ├── config.py        # Configuration
│       ├── database.py      # SQLite database layer
│       ├── main.py          # FastAPI app + endpoints
│       └── rate_limiter.py  # Token bucket rate limiter
│
├── models/                   # Model files (created at runtime)
│   └── ena.tiny.v1.json     # Model metadata
│
├── tests/                    # Unit tests
│   ├── test_address.py
│   ├── test_model_registry.py
│   └── test_rate_limiter.py
│
├── __init__.py
├── inference.py              # Inference engine
├── model_registry.py         # Model management
├── .env.example              # Configuration template
├── Dockerfile                # Container image
├── docker-compose.yml        # Container orchestration
├── ena-node.service          # Systemd service unit
├── requirements.txt          # Python dependencies
├── smoke_test.sh             # Basic functionality test
├── README.md                 # User documentation
└── USAGE.md                  # End-user guide
```

## Development Setup

### 1. Clone and Install

```bash
# Clone repository
git clone https://github.com/animicaorg/all.git
cd all

# Install dependencies
pip install -e python/
pip install -r ena/requirements.txt

# Install dev dependencies
pip install pytest pytest-asyncio
```

### 2. Configuration

```bash
# Copy environment template
cp ena/.env.example ena/.env

# Edit configuration
nano ena/.env
```

Key settings for development:
```bash
# Enable dev mode (skips payment verification)
ENA_DEV_MODE=1

# Use local RPC
ENA_RPC_URL=http://localhost:8545/rpc

# Set test service address
ENA_SERVICE_ADDRESS=anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq000000

# Low fees for testing
ENA_FEE_PER_CALL=1000000
ENA_FEE_PER_TOKEN=100
```

### 3. Run Development Server

```bash
# Set environment
export ENA_DEV_MODE=1

# Start server
cd all
python -m ena.services.ena_node.main
```

Server will start on http://localhost:8080

### 4. Test with CLI

```bash
# In another terminal
export ENA_ENDPOINT=http://localhost:8080
export ENA_DEV_MODE=1

# Test commands
animica ena models
animica ena pricing
animica ena infer "Hello!" --fee-mode per_call_tx
```

## Running Tests

### Unit Tests

```bash
# Run all ENA tests
pytest ena/tests/ -v

# Run specific test file
pytest ena/tests/test_address.py -v

# Run with coverage
pytest ena/tests/ --cov=ena --cov-report=html
```

### Smoke Test

```bash
# Basic functionality test
cd ena
./smoke_test.sh
```

### Integration Tests

TODO: Add integration tests with mock RPC server

## API Endpoints

### Health Check

```bash
curl http://localhost:8080/v1/health
```

Response:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "dev_mode": true
}
```

### List Models

```bash
curl http://localhost:8080/v1/models
```

### Get Pricing

```bash
curl http://localhost:8080/v1/pricing
```

### Run Inference

```bash
curl -X POST http://localhost:8080/v1/infer \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Hello, world!",
    "max_tokens": 50,
    "payment": {
      "mode": "per_call_tx",
      "payer": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq000000",
      "tx_hash": "0x0000000000000000000000000000000000000000000000000000000000000001"
    }
  }'
```

### Admin: Set Default Model

```bash
curl -X POST http://localhost:8080/admin/set_default_model \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_name": "ena.tiny.v1"}'
```

### Admin: Set Alias

```bash
curl -X POST http://localhost:8080/admin/set_alias \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"alias": "ena.latest", "target": "ena.tiny.v1"}'
```

## Database Schema

### used_transactions
Tracks transaction hashes used for inference (replay protection)

```sql
CREATE TABLE used_transactions (
    tx_hash TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    amount INTEGER NOT NULL,
    used_at REAL NOT NULL,
    request_id TEXT NOT NULL
);
```

### credit_balances
Tracks credit balances for credit mode

```sql
CREATE TABLE credit_balances (
    address TEXT PRIMARY KEY,
    balance INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
```

### request_logs
Audit log of all inference requests

```sql
CREATE TABLE request_logs (
    request_id TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    tx_hash TEXT,
    amount_paid INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    success INTEGER NOT NULL,
    error TEXT
);
```

## Adding New Models

### 1. Create Model Metadata

Create a JSON file in `ena/models/`:

```json
{
  "name": "ena.medium.v1",
  "version": "1.0.0",
  "path": "./ena/models/medium.bin",
  "tokenizer": "gpt2",
  "max_tokens": 1000,
  "description": "Medium-size model for general use",
  "created_at": "2024-01-15T00:00:00Z"
}
```

### 2. Add Model Files

Place the actual model file at the path specified in metadata.

### 3. Update Inference Engine

Modify `ena/inference.py` to support the new model format:

```python
def _load_model(self):
    """Load the model."""
    # Add support for your model format
    if self.model_name.startswith("ena.medium"):
        # Load medium model
        pass
```

### 4. Reload Models

```bash
curl -X POST http://localhost:8080/admin/reload_models \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

## Payment Verification Flow

### Per-Call Transaction Mode

1. User creates payment transaction via CLI
2. Transaction is submitted to blockchain
3. User includes tx_hash in inference request
4. ENA service:
   - Checks if tx_hash was already used (replay protection)
   - Fetches transaction from blockchain via RPC
   - Verifies `to` matches service address
   - Verifies `from` matches payer in request
   - Verifies `value` >= minimum fee
   - Marks tx_hash as used
5. Runs inference and returns result

### Credit Mode

1. User deposits funds to service address
2. Service detects deposit and adds credits to balance
3. User includes signature in inference request
4. ENA service:
   - Verifies signature
   - Checks credit balance
   - Deducts estimated cost upfront
   - Runs inference
   - Refunds excess credits
5. Returns result

## Rate Limiting

Uses token bucket algorithm:

```python
# Per address: 100 requests/hour
# Refill rate: 100/3600 = 0.0278 tokens/second

# Per IP: 200 requests/hour  
# Refill rate: 200/3600 = 0.0556 tokens/second
```

Limits are checked before payment verification.

## Circuit Breaker

Protects against RPC outages:

- After 5 consecutive failures, circuit opens
- Requests fail immediately with clear error
- After 60 seconds, circuit enters half-open state
- One successful request closes circuit

## Security Considerations

### Input Validation

- Address format: bech32 (anim1 + 39 chars)
- Transaction hash: 0x + 64 hex characters
- Prompt length: Max 2000 characters
- Max tokens: Max 500 per request

### Replay Protection

- Each tx_hash can only be used once
- Tracked in `used_transactions` table
- No time limit (permanent record)

### Rate Limiting

- Per address and per IP limits
- Token bucket refills over time
- Prevents abuse and DoS attacks

### Dev Mode

⚠️ **NEVER USE IN PRODUCTION** ⚠️

```bash
# Dev mode disables:
# - Payment verification
# - Transaction checks
# - Credit deductions

# Only use for local testing!
ENA_DEV_MODE=1
```

## Docker Deployment

### Build Image

```bash
cd all
docker build -f ena/Dockerfile -t ena-node:latest .
```

### Run Container

```bash
docker run -d \
  --name ena-node \
  -p 8080:8080 \
  -e ENA_RPC_URL=https://mainnet.animica.org/rpc \
  -e ENA_SERVICE_ADDRESS=anim1... \
  -e ENA_ADMIN_TOKEN=secret \
  -v ena-data:/data \
  ena-node:latest
```

### Docker Compose

```bash
cd ena
docker-compose up -d
```

## Systemd Deployment

```bash
# Copy service file
sudo cp ena/ena-node.service /etc/systemd/system/

# Create service user
sudo useradd -r -s /bin/false ena

# Create directories
sudo mkdir -p /var/lib/ena
sudo chown ena:ena /var/lib/ena

# Create config
sudo mkdir -p /etc/ena
sudo cp ena/.env.example /etc/ena/ena.env
sudo nano /etc/ena/ena.env

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ena-node
sudo systemctl start ena-node

# Check status
sudo systemctl status ena-node
```

## Logging

Logs are written to:
- Console (stdout/stderr)
- File (configured via `ENA_LOG_FILE`)

Log levels:
- DEBUG: Detailed debug information
- INFO: Normal operations
- WARNING: Warning messages
- ERROR: Error messages

Example log entry:
```
2024-01-15 10:30:45 - ena.services.ena_node.main - INFO - Inference request: 550e8400-e29b-41d4-a716-446655440000
```

## Performance Tuning

### Database Optimization

```python
# Add indexes for common queries
CREATE INDEX idx_request_logs_timestamp ON request_logs(timestamp);
CREATE INDEX idx_request_logs_payer_timestamp ON request_logs(payer, timestamp);
```

### Rate Limiter Cleanup

```python
# Periodically clean old buckets
limiter.cleanup_old_buckets(max_age=7200)
```

### Model Caching

Inference engines are cached per model:

```python
if model_info.name not in inference_engines:
    inference_engines[model_info.name] = create_inference_engine(...)
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
tail -f ena_data/ena.log

# Check permissions
ls -la ena_data/
chmod 644 ena_data/ena.db

# Check Python version
python --version  # Should be 3.10+
```

### Payment Verification Fails

```bash
# Test RPC connection
curl https://mainnet.animica.org/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'

# Check circuit breaker state
# Look for "Circuit breaker opened" in logs
```

### Rate Limit Issues

```bash
# Check current limits
# Look at rate_limiter initialization in logs

# Adjust limits via environment
export ENA_RATE_LIMIT_PER_ADDRESS=500
export ENA_RATE_LIMIT_PER_IP=1000
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Write tests for new features
4. Ensure all tests pass
5. Submit a pull request

### Code Style

- Follow PEP 8 for Python code
- Use type hints where appropriate
- Add docstrings to functions and classes
- Keep functions focused and small

### Testing Requirements

- Unit tests for all new functions
- Integration tests for API endpoints
- Maintain >80% code coverage

## Future Enhancements

- [ ] Support for streaming inference
- [ ] WebSocket API
- [ ] Multiple model backends (ONNX, TensorFlow, etc.)
- [ ] GPU support
- [ ] Distributed inference
- [ ] Model marketplace
- [ ] Usage analytics dashboard
- [ ] Prometheus metrics
- [ ] OpenAPI/Swagger documentation

## License

See LICENSE.txt in the repository root.
