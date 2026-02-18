# ENA Service Usage Guide

This guide demonstrates how miners and users can interact with the ENA inference service using the Animica CLI.

## Prerequisites

1. **Animica CLI installed** with ENA integration
2. **Animica wallet** with some ANM for payments
3. **ENA service endpoint** (default: https://ena.animica.org)

## Getting Started

### 1. Check Available Models

```bash
animica ena models
```

Output:
```
┌────────────────┬─────────┬────────────┬─────────────────────────────┐
│ Name           │ Version │ Max Tokens │ Description                 │
├────────────────┼─────────┼────────────┼─────────────────────────────┤
│ ena.tiny.v1    │ 0.1.0   │ 500        │ Dummy model for testing     │
└────────────────┴─────────┴────────────┴─────────────────────────────┘

Aliases:
  ena.latest → ena.tiny.v1

Default: ena.tiny.v1
```

### 2. Check Pricing

```bash
animica ena pricing
```

Output:
```
ENA Pricing:
  Base fee per call: 0.01 ANM
  Fee per output token: 0.000001 ANM

Example: A call generating 100 tokens costs:
  0.0101 ANM
```

### 3. Run Inference (Per-Call Transaction Mode)

This is the simplest mode - each inference request requires a payment transaction.

```bash
animica ena infer "What is blockchain?" --fee-mode per_call_tx
```

The CLI will:
1. Automatically create a payment transaction from your default wallet
2. Send it to the ENA service address
3. Wait for confirmation
4. Submit the inference request
5. Display the results

Output:
```
Sending payment transaction...
  From: anim1qy2j...
  To: anim1qqqq...
  Amount: 0.01 ANM
✓ Payment transaction: 0xabc123...
Waiting for transaction confirmation...
Running inference...

✓ Inference complete!

Response:
Blockchain is a distributed ledger technology that records transactions...

Usage:
  Prompt tokens: 3
  Completion tokens: 15
  Total tokens: 18

Receipt:
  ID: 550e8400-e29b-41d4-a716-446655440000
  Mode: per_call_tx
  Amount paid: 0.010015 ANM
  Transaction: 0xabc123...
```

### 4. Run Inference with Specific Options

```bash
animica ena infer \
  "Explain consensus mechanisms" \
  --model ena.latest \
  --max-tokens 200 \
  --from my-wallet \
  --json
```

Options:
- `--model`: Specify model name or alias
- `--max-tokens`: Maximum tokens to generate (default: 100)
- `--from`: Wallet to use (address, label, or index)
- `--json`: Output as JSON for scripting
- `--endpoint`: Override ENA endpoint URL
- `--rpc-url`: Override Animica RPC URL

### 5. Deposit Credits (Credit Mode)

For frequent usage, deposit credits upfront to avoid per-transaction overhead:

```bash
# Deposit 10 ANM
animica ena deposit 10

# Or specify from wallet
animica ena deposit 5 --from my-mining-wallet
```

Output:
```
Depositing credits...
  From: anim1qy2j...
  To: anim1qqqq...
  Amount: 10 ANM

✓ Deposit transaction sent!
  Transaction: 0xdef456...
  Amount: 10 ANM

Credits will be available once the transaction is confirmed.
```

### 6. Run Inference with Credits

Once you have credits, use credit mode for faster inference:

```bash
animica ena infer "Hello, world!" --fee-mode credit
```

Benefits:
- No transaction per call
- Faster response times
- Better for batch operations
- Automatic refunds for unused credits

## Advanced Usage

### Batch Processing with Credits

```bash
# Deposit credits for batch work
animica ena deposit 100

# Run multiple inferences
for prompt in "Question 1" "Question 2" "Question 3"; do
  animica ena infer "$prompt" --fee-mode credit --json >> results.json
done
```

### Check Transaction Status

```bash
animica ena status 0xabc123...
```

### Monitoring Usage

All requests are logged in the ENA database. For your own records, save outputs:

```bash
# Save with metadata
animica ena infer "My prompt" --json > inference_$(date +%s).json
```

## Environment Variables

Configure defaults via environment variables:

```bash
# Set custom ENA endpoint
export ENA_ENDPOINT=http://localhost:8080

# Set custom service address (for testing)
export ENA_SERVICE_ADDRESS=anim1test...

# Set custom RPC endpoint
export ANIMICA_RPC_URL=http://localhost:8545/rpc
```

## Wallet Management

### List Wallets

```bash
animica wallet list
```

### Create New Wallet

```bash
animica wallet new --label mining-wallet
```

### Check Balance

```bash
animica wallet balance anim1qy2j...
```

## Error Handling

### Insufficient Balance

```
Error: Insufficient balance for transaction
```

**Solution**: Add funds to your wallet via faucet or transfer

### Rate Limited

```
Error: Rate limit exceeded
```

**Solution**: Wait before retrying. Default limits:
- 100 requests/hour per address
- 200 requests/hour per IP

### Transaction Already Used

```
Error: Transaction already used (replay protection)
```

**Solution**: The transaction was already used for a previous inference. Submit a new payment transaction.

### RPC Unavailable

```
Error: RPC service unavailable - cannot verify payment
```

**Solution**: The Animica RPC is temporarily down. Try again later or use a different RPC endpoint.

## Best Practices

1. **Use Credit Mode for Batch Work**: Deposit once, run many inferences
2. **Monitor Your Balance**: Check wallet balance regularly
3. **Set Reasonable max-tokens**: Higher limits cost more
4. **Save Results**: Use `--json` and save to files for records
5. **Handle Errors**: Implement retry logic in scripts

## Pricing Breakdown

| Operation | Cost | Notes |
|-----------|------|-------|
| Base fee per call | 0.01 ANM | Fixed per inference request |
| Per output token | 0.000001 ANM | Charged per token generated |
| Transaction fees | ~0.00002 ANM | Standard Animica tx fee |

Example costs:
- 50 token response: ~0.01005 ANM
- 100 token response: ~0.0101 ANM
- 200 token response: ~0.0102 ANM

## Troubleshooting

### CLI Not Found

```bash
# Reinstall animica CLI
cd python
pip install -e .
```

### Module Import Errors

```bash
# Install dependencies
pip install httpx typer rich
```

### Permission Denied

```bash
# Check wallet file permissions
chmod 600 ~/.animica/wallets.json
```

## Support

For issues or questions:
- Check the ENA README: `ena/README.md`
- Review the main documentation
- Contact support via Animica Discord

## Example Scripts

### Python Script

```python
import subprocess
import json

def run_inference(prompt, max_tokens=100):
    """Run ENA inference and return parsed result."""
    result = subprocess.run(
        [
            "animica", "ena", "infer",
            prompt,
            "--max-tokens", str(max_tokens),
            "--fee-mode", "credit",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        raise Exception(f"Inference failed: {result.stderr}")
    
    return json.loads(result.stdout)

# Usage
response = run_inference("What is Animica?")
print(f"Answer: {response['answer']}")
print(f"Cost: {response['receipt']['amount']} base units")
```

### Bash Script

```bash
#!/bin/bash
# Batch inference script

PROMPTS_FILE="prompts.txt"
OUTPUT_DIR="results"

mkdir -p "$OUTPUT_DIR"

# Deposit credits
animica ena deposit 10

# Process each prompt
while IFS= read -r prompt; do
    timestamp=$(date +%s)
    output_file="$OUTPUT_DIR/result_${timestamp}.json"
    
    echo "Processing: $prompt"
    
    animica ena infer "$prompt" \
        --fee-mode credit \
        --json > "$output_file"
    
    if [ $? -eq 0 ]; then
        echo "✓ Saved to $output_file"
    else
        echo "✗ Failed"
    fi
    
    sleep 1  # Rate limiting
done < "$PROMPTS_FILE"

echo "Batch complete!"
```

## Security Notes

1. **Never share your wallet files** (`~/.animica/wallets.json`)
2. **Use strong passwords** for encrypted wallets
3. **Keep backups** of your wallet files
4. **Monitor usage** to detect unauthorized access
5. **Rotate keys** periodically for high-value wallets

## Rate Limits

Default limits (configurable by service):
- **Per Address**: 100 requests/hour
- **Per IP**: 200 requests/hour

For higher limits, contact service operators.
