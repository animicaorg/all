FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY animica_bridge/ /app/animica_bridge/

# Create non-root user
RUN useradd -m -u 1000 bridgeuser && \
    chown -R bridgeuser:bridgeuser /app

USER bridgeuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run bridge service
CMD ["python", "-m", "animica_bridge.main"]
