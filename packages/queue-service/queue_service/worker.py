"""
Celery Worker Configuration

Main Celery application for background task processing.
"""

from celery import Celery
from celery.schedules import crontab
import os

# Get configuration from environment
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://animica:animica_dev_password@rabbitmq:5672/")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Create Celery app
celery_app = Celery(
    "animica_compute_queue",
    broker=RABBITMQ_URL,
    backend=REDIS_URL,
    include=[
        "queue_service.tasks.inference",
        "queue_service.tasks.sandbox",
        "queue_service.tasks.billing",
        "queue_service.tasks.github",
        "queue_service.tasks.models",
        "queue_service.tasks.maintenance",
    ]
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3300,  # 55 minutes soft limit
    
    # Worker settings
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    
    # Result backend
    result_expires=86400,  # 24 hours
    result_persistent=True,
    
    # Retry settings
    task_default_max_retries=3,
    task_default_retry_delay=60,
    
    # Beat schedule for periodic tasks
    beat_schedule={
        # Aggregate usage metrics every hour
        "aggregate-usage-metrics": {
            "task": "queue_service.tasks.billing.aggregate_usage",
            "schedule": crontab(minute=0),  # Every hour
        },
        
        # Clean up old completed tasks daily
        "cleanup-old-tasks": {
            "task": "queue_service.tasks.maintenance.cleanup_old_tasks",
            "schedule": crontab(hour=2, minute=0),  # 2 AM daily
        },
        
        # Update model registry every 6 hours
        "update-model-registry": {
            "task": "queue_service.tasks.models.update_registry",
            "schedule": crontab(minute=0, hour="*/6"),  # Every 6 hours
        },
        
        # Process pending GitHub operations every 5 minutes
        "process-github-queue": {
            "task": "queue_service.tasks.github.process_pending_operations",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
        },
    },
)

# Priority queues
celery_app.conf.task_routes = {
    "queue_service.tasks.inference.*": {"queue": "inference"},
    "queue_service.tasks.sandbox.*": {"queue": "sandbox"},
    "queue_service.tasks.billing.*": {"queue": "billing"},
    "queue_service.tasks.github.*": {"queue": "github"},
    "queue_service.tasks.models.*": {"queue": "models"},
    "queue_service.tasks.maintenance.*": {"queue": "maintenance"},
}

if __name__ == "__main__":
    celery_app.start()
