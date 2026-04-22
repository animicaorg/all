# AICF Web

A full website + app UI for `aicf.animica.org`.

## Includes

- Public pages (`/`, `/pricing`, `/docs`, `/providers`, `/ecosystem`, `/about`, `/faq`, `/support`, `/status`)
- Mining contribution page with executable downloads (`/contribute/mining`)
- Developer dashboard pages under `/app/*`
- AICF contract studio + chat (`/app/studio`)
- Contract AI orchestration pages:
  - `/app/contracts`
  - `/app/contract-jobs`
  - `/app/agent-tasks`
  - `/app/disputes`
  - `/app/provider-jobs`
- Provider dashboard pages under `/provider/*`
- Admin console pages under `/admin/*`
- Wallet connect and direct contract call support

## Run

```bash
cp .env.example .env
pnpm --filter @animica/aicf-api dev
pnpm --filter @animica/aicf-web dev
```
