FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Note: In production, use gVisor or run as unprivileged user
# For now, this is a basic sandbox

CMD ["uvicorn", "sandbox_runner.main:app", "--host", "0.0.0.0", "--port", "8004"]
