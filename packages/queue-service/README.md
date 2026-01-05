# Queue Service

Background task processing using Celery for the Animica Compute Platform.

## Features

- **Asynchronous Task Processing**: Long-running operations
- **Job Queue Management**: Prioritized task execution
- **Scheduled Tasks**: Periodic jobs (e.g., cleanup, metrics)
- **Retry Logic**: Automatic retry on failures
- **Monitoring**: Task status tracking

## Architecture

```
API/Services → RabbitMQ → Celery Workers → Tasks
                  ↓
               Redis (results backend)
```

## Task Types

1. **Inference Tasks**: Long-running LLM inference jobs
2. **Sandbox Tasks**: Code execution in isolated environments
3. **Model Tasks**: Model downloads, conversions, evaluations
4. **Billing Tasks**: Usage aggregation, invoice generation
5. **GitHub Tasks**: Repository operations, PR creation
6. **Maintenance Tasks**: Cleanup, backups, metrics

## Development

### Setup

```bash
cd packages/queue-service
pip install -r requirements.txt
```

### Run Worker

```bash
celery -A queue_service.worker worker --loglevel=info
```

### Run Beat (Scheduler)

```bash
celery -A queue_service.worker beat --loglevel=info
```

### Monitor Tasks

```bash
celery -A queue_service.worker flower  # Web UI at http://localhost:5555
```

## Environment Variables

```
RABBITMQ_URL=amqp://animica:password@localhost:5672/
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=amqp://animica:password@localhost:5672/
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```
