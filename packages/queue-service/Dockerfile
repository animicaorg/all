FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY queue_service/ /app/queue_service/

# Create non-root user
RUN useradd -m -u 1000 celeryuser && \
    chown -R celeryuser:celeryuser /app

USER celeryuser

# Default command (can be overridden)
CMD ["celery", "-A", "queue_service.worker", "worker", "--loglevel=info"]
