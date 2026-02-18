# ENA Workers

Workers for executing ENA upgrade jobs: training, evaluation, and distillation.

## Quick Start

### MOCK Mode (Testing)

```bash
# Training
python -m ena.workers.train_worker --job-spec examples/train_job.json --mock

# Evaluation
python -m ena.workers.eval_worker --job-spec examples/eval_job.json --mock

# Distillation
python -m ena.workers.distill_worker --job-spec examples/distill_job.json --mock
```

### Docker

```bash
# Build
docker build -f Dockerfile.worker -t ena-worker:gpu .

# Run
docker run --gpus all -v $(pwd)/jobs:/jobs ena-worker:gpu \
  python -m ena.workers.train_worker --job-spec /jobs/train.json --mock
```

## Workers

- **TrainingWorker** - Supervised fine-tuning with LoRA
- **EvaluationWorker** - Model evaluation (accuracy, perplexity, toxicity, regression)
- **DistillationWorker** - Knowledge distillation + GGUF quantization

## Job Specs

See parent directory `ENA_WORKERS_TELEMETRY_IMPLEMENTATION.md` for detailed job spec formats.

## MOCK Mode

All workers support `--mock` flag for testing without real compute:
- No GPU required
- Fast execution (seconds)
- Realistic dummy outputs
- No external dependencies

## Real Mode

Real mode requires:
- PyTorch + CUDA
- HuggingFace Transformers
- Training datasets
- DA layer integration (TODO)

Currently raises `NotImplementedError` - use MOCK mode for testing.
