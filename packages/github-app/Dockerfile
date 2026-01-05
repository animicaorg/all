FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 githubuser && chown -R githubuser:githubuser /app
USER githubuser

CMD ["uvicorn", "github_app.main:app", "--host", "0.0.0.0", "--port", "8005"]
