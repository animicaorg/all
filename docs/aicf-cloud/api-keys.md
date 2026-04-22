# API Keys

- Create project-scoped API keys via `POST /projects/:id/api-keys`.
- Keys are returned once and hashed server-side.
- Scope model:
  - `inference:chat`
  - `inference:embeddings`
  - `jobs:write`
  - `jobs:read`
  - `projects:read`
  - `projects:write`
- Revoke with `POST /projects/api-keys/:keyId/revoke`.
