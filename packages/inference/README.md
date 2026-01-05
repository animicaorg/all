# Animica Compute Platform - Inference Service

OpenAI-compatible LLM inference service with vLLM backend for GPU and CPU fallback for development.

## Features

- **OpenAI-Compatible API**: Drop-in replacement for OpenAI API
- **Streaming Support**: Server-sent events for real-time responses
- **Multiple Models**: Support for various LLMs (Llama, Mistral, etc.)
- **GPU Acceleration**: vLLM for high-throughput GPU inference
- **CPU Fallback**: Hugging Face Transformers for development without GPU
- **Usage Tracking**: Integration with billing service
- **Model Warming**: Keep hot models in memory
- **Batch Processing**: Efficient batching for throughput

## Supported Models

### Production (GPU Required)
- `llama-3-8b-instruct`: Meta Llama 3 8B (default)
- `mistral-7b-instruct`: Mistral 7B Instruct
- `codellama-13b`: Code Llama 13B

### Development (CPU Fallback)
- `gpt2`: GPT-2 (small model for testing)
- `distilgpt2`: DistilGPT-2 (even smaller)

## API Endpoints

### OpenAI-Compatible

- `POST /v1/chat/completions` - Chat completion (streaming supported)
- `POST /v1/completions` - Text completion
- `POST /v1/embeddings` - Generate embeddings
- `GET /v1/models` - List available models

### Health & Metrics

- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics

## Quick Start

### CPU Mode (Development)

```bash
cd packages/inference
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m inference.main --host 0.0.0.0 --port 8003
```

### GPU Mode (Production)

```bash
# Build GPU image
docker build -f Dockerfile.gpu -t animica/inference:gpu .

# Run with GPU
docker run --gpus all -p 8003:8003 \
  -e MODEL_NAME=llama-3-8b-instruct \
  -v /models:/models \
  animica/inference:gpu
```

## Usage Examples

### Chat Completion

```bash
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3-8b-instruct",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is the capital of France?"}
    ],
    "max_tokens": 100,
    "temperature": 0.7
  }'
```

### Streaming Chat

```bash
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3-8b-instruct",
    "messages": [
      {"role": "user", "content": "Write a short poem about AI"}
    ],
    "stream": true
  }'
```

### List Models

```bash
curl http://localhost:8003/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Architecture

### With vLLM (GPU)

```
Client Request
     │
     ▼
┌─────────────────┐
│  FastAPI        │
│  API Server     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  vLLM Engine    │
│  - Paged Attn   │
│  - Continuous   │
│  - Batching     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  GPU Memory     │
│  (Model Loaded) │
└─────────────────┘
```

### CPU Fallback (Development)

```
Client Request
     │
     ▼
┌─────────────────┐
│  FastAPI        │
│  API Server     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Transformers   │
│  Pipeline       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  CPU Memory     │
│  (Model Loaded) │
└─────────────────┘
```

## Configuration

Environment variables:

- `MODEL_PATH`: Path to model files (default: `/models`)
- `DEFAULT_MODEL`: Default model to use (default: `llama-3-8b-instruct`)
- `GPU_ENABLED`: Enable GPU inference (default: `false`)
- `MAX_MODEL_LEN`: Max sequence length (default: `4096`)
- `GPU_MEMORY_UTILIZATION`: GPU memory fraction (default: `0.9`)
- `TENSOR_PARALLEL_SIZE`: Number of GPUs (default: `1`)

## Model Management

### Download Models

```bash
# Llama 3 8B
huggingface-cli download meta-llama/Meta-Llama-3-8B-Instruct \
  --local-dir /models/llama-3-8b-instruct

# Mistral 7B
huggingface-cli download mistralai/Mistral-7B-Instruct-v0.2 \
  --local-dir /models/mistral-7b-instruct
```

### Model Registry Integration

Models are registered in the model-registry service with:
- Version tags
- Performance metrics (latency, throughput)
- Resource requirements
- Compatibility flags

## Performance

### GPU Inference (vLLM)

- **Throughput**: 1000+ tokens/sec on A100
- **Latency**: 50-100ms first token
- **Batch Size**: Dynamic batching for optimal throughput
- **Memory**: PagedAttention reduces memory by 2-4x

### CPU Inference (Fallback)

- **Throughput**: 10-50 tokens/sec
- **Latency**: 500-1000ms first token
- **Memory**: Requires 8-16GB RAM for 7B models

## Monitoring

### Metrics

- `inference_requests_total`: Total requests
- `inference_request_duration_seconds`: Request duration histogram
- `inference_tokens_generated`: Total tokens generated
- `inference_active_requests`: Current active requests
- `inference_gpu_memory_used`: GPU memory usage (if available)

### Logging

All requests logged with:
- Model name
- Token count (prompt + completion)
- Duration
- User/org ID for billing

## Integration with Billing

Each inference request:
1. Extracts user ID from JWT/API key
2. Counts tokens (prompt + completion)
3. Calculates cost based on model pricing
4. Records usage in billing service
5. Deducts credits from user balance

## Security

- API key validation via auth service
- Rate limiting per user/org
- Input validation (max tokens, temperature range)
- No model file access for users
- Sandboxed inference process

## Deployment

### Single GPU

```yaml
# docker-compose excerpt
inference-service:
  image: animica/inference:gpu
  runtime: nvidia
  environment:
    - NVIDIA_VISIBLE_DEVICES=0
    - MODEL_NAME=llama-3-8b-instruct
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

### Multi-GPU (Tensor Parallel)

```bash
# 4x A100 GPUs
docker run --gpus all -p 8003:8003 \
  -e TENSOR_PARALLEL_SIZE=4 \
  -e MODEL_NAME=llama-3-70b-instruct \
  animica/inference:gpu
```

### Kubernetes

See `ops/k8s/inference-service/` for manifests with:
- Node affinity for GPU nodes
- Resource requests/limits
- HPA for auto-scaling
- PVC for model storage

## Troubleshooting

### Out of Memory

- Reduce `MAX_MODEL_LEN`
- Lower `GPU_MEMORY_UTILIZATION`
- Use smaller model variant
- Enable tensor parallelism

### Slow Inference

- Check GPU utilization (`nvidia-smi`)
- Verify model is on GPU (check logs)
- Increase batch size for throughput
- Use vLLM instead of transformers

### Model Loading Issues

- Check model path exists
- Verify model format (HF compatible)
- Ensure sufficient disk space
- Check model permissions

## Development

```bash
# Install dev dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio black ruff

# Run tests
pytest tests/ -v

# Format code
black inference/
ruff check inference/ --fix

# Run with hot reload
uvicorn inference.main:app --reload --port 8003
```

## License

See LICENSE.txt in repository root.
