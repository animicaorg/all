# Local Development

## 1. Contracts tests

```bash
pytest -q contracts/tests/test_usdan_contracts.py
```

## 2. Backend

```bash
pnpm --filter @animica/usdan-api test
pnpm --filter @animica/usdan-api build
pnpm --filter @animica/usdan-api dev
```

## 3. Frontend

```bash
pnpm --filter @animica/usdan-web test
pnpm --filter @animica/usdan-web build
pnpm --filter @animica/usdan-web dev
```

## 4. End-to-end local stack

```bash
docker compose -f ops/docker/docker-compose.usdan.yml up -d --build
```
