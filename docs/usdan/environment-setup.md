# Environment Setup

## Backend

Copy and edit:

```bash
cp services/usdan-api/.env.example services/usdan-api/.env
```

Required variables are documented in that file, including:
- API auth/rate limit
- Animica chain/mint controller addresses
- Modern Treasury credentials
- reserve thresholds

## Frontend

Create `apps/usdan-web/.env` with:

```bash
VITE_USDAN_API_BASE_URL=http://127.0.0.1:8098
VITE_ANIMICA_CHAIN_ID=1337
VITE_USDAN_TOKEN_ADDRESS=anim1_usdan_token
VITE_USDAN_ADMIN_API_KEY=replace-with-admin-api-key
```
