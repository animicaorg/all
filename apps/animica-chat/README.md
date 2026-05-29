# Animica Chat

A focused AI workspace. **$10/month, paid in USD via PayPal. No ANM required.**

The product replaces the marketing homepage at `animica.org/` with:
- A chat-first landing page (HomePage) keeping the existing site nav links.
- A streamed, ChatGPT-style chat UI at `/chat` and `/chat/:id`.
- Multiple assistant modes (general, coding, marketing, Animica blockchain helper, art prompts, business).
- A **coding agent** that can read repos and open draft PRs / MRs on the user's behalf — backed by linked **GitHub** and **GitLab** accounts.
- A `$10/mo` PayPal subscription, admin dashboard, billing page, account page, and tools page.
- A separate **local CLI** (`apps/animica-chat/cli/`) that pairs an API key with a user account and runs an interactive coding agent in the terminal.

## Architecture

```
apps/animica-chat/
├── package.json                  # workspace root (pnpm + concurrently dev)
├── .env.example                  # every env var documented
├── prisma/
│   ├── schema.prisma             # 16 models incl. Conversation, Message,
│   │                             # UserSubscription, AgentToolCall, GitHubLink, …
│   └── seed.ts                   # one $10/mo Pro plan + the assistant-mode prompts
├── server/                       # Node + Express + TypeScript + Prisma
│   └── src/
│       ├── env.ts                # zod-validated env, fails fast on bad config
│       ├── prisma.ts             # singleton client
│       ├── index.ts              # bootstrap
│       ├── lib/
│       │   ├── tokens.ts         # opaque session tokens (SHA-256 at rest)
│       │   └── secretBox.ts      # AES-256-GCM for OAuth tokens at rest
│       ├── middleware/
│       │   ├── auth.ts           # session cookie → req.user, requireAuth, …
│       │   └── rateLimit.ts
│       ├── services/
│       │   ├── aiProvider.ts     # OpenAI-compatible streaming + chat
│       │   ├── systemPrompts.ts  # assistant-mode catalog
│       │   ├── toolRegistry.ts   # OpenAI-style tool schema + zod validation
│       │   ├── agentLoop.ts      # the tool-calling loop (model ↔ tools ↔ model)
│       │   ├── integrations/
│       │   │   ├── github.ts     # OAuth + Octokit-backed tool execution
│       │   │   └── gitlab.ts     # OAuth + @gitbeaker/rest tool execution
│       │   ├── paypalClient.ts   # subscriptions, webhook signature verify
│       │   ├── usage.ts          # free-daily + Pro-monthly caps
│       │   └── mailer.ts         # console + SMTP magic-link mailer
│       └── routes/
│           ├── auth.ts           # request-link, verify, logout, /me
│           ├── paypal.ts         # create-subscription + webhook handler
│           ├── billing.ts        # status, cancel, plans
│           ├── chat.ts           # SSE chat endpoint — runs the agent loop
│           ├── conversations.ts  # list / read / rename / archive / delete
│           ├── integrations.ts   # OAuth start/callback/disconnect/status
│           ├── admin.ts          # users, subs, usage, system-prompts
│           └── health.ts
├── web/                          # React + Vite + Tailwind
│   └── src/
│       ├── lib/{api,auth,chatStream}.ts
│       ├── components/{SiteHeader,MarkdownMessage,MessageRow,
│       │              ConversationSidebar,AssistantModePicker,ToolCallCard}.tsx
│       ├── pages/{Home,Chat,Login,Pricing,Account,Billing,Admin,Tools}.tsx
│       └── App.tsx               # routes + ProtectedRoute
├── cli/                          # Coding agent CLI (`animica-chat`)
└── deploy/                       # nginx vhost + systemd unit (examples)
```

## Local development

```bash
# 1. Install deps (we use pnpm; npm/yarn work too).
pnpm install

# 2. Stand up Postgres locally — e.g. via Docker.
docker run -d --name animica-chat-pg -p 5432:5432 \
  -e POSTGRES_USER=animica_chat \
  -e POSTGRES_PASSWORD=animica_chat \
  -e POSTGRES_DB=animica_chat \
  postgres:16

# 3. Copy .env.example → .env and fill in JWT_SECRET (rand-hex 32) + AI_API_KEY.
#    The other vars can stay as defaults for local dev; PayPal + GitHub +
#    GitLab credentials only matter once you exercise those flows.
cp .env.example .env
openssl rand -hex 32  # paste into JWT_SECRET

# 4. Push schema + seed.
pnpm prisma:migrate
pnpm prisma:seed

# 5. Run both server + web with HMR.
pnpm dev
# server: http://localhost:4400
# web:    http://localhost:5173 (proxies /api → 4400)
```

The magic-link mailer defaults to `MAIL_DRIVER=console` — open the
server log to copy the link onto stdout.

## Setup recipes

### PayPal Subscriptions (sandbox)

1. Create a Sandbox Business app at https://developer.paypal.com/dashboard/applications.
2. Copy the Client ID + Secret into `PAYPAL_CLIENT_ID` + `PAYPAL_CLIENT_SECRET`. Leave `PAYPAL_ENV=sandbox`.
3. Create a Product + a $10/month Plan via the REST API (the dashboard's UI only covers the legacy classic flow):

   ```bash
   # Get a token
   TOKEN=$(curl -s -u "$PAYPAL_CLIENT_ID:$PAYPAL_CLIENT_SECRET" \
     -H "Accept: application/json" \
     -d "grant_type=client_credentials" \
     https://api-m.sandbox.paypal.com/v1/oauth2/token | jq -r .access_token)

   # Create a product
   PRODUCT_ID=$(curl -s -X POST https://api-m.sandbox.paypal.com/v1/catalogs/products \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"name":"Animica Chat Pro","type":"SERVICE","category":"SOFTWARE"}' | jq -r .id)

   # Create the plan
   curl -s -X POST https://api-m.sandbox.paypal.com/v1/billing/plans \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d "{
       \"product_id\":\"$PRODUCT_ID\",
       \"name\":\"Pro Monthly\",
       \"billing_cycles\":[{
         \"frequency\":{\"interval_unit\":\"MONTH\",\"interval_count\":1},
         \"tenure_type\":\"REGULAR\",\"sequence\":1,\"total_cycles\":0,
         \"pricing_scheme\":{\"fixed_price\":{\"value\":\"20\",\"currency_code\":\"USD\"}}
       }],
       \"payment_preferences\":{\"auto_bill_outstanding\":true,\"setup_fee_failure_action\":\"CONTINUE\",\"payment_failure_threshold\":3}
     }" | jq -r .id
   ```

4. Drop the returned plan id into `PAYPAL_PLAN_ID_PRO`.
5. Register a webhook URL — `<PUBLIC_BASE_URL>/api/webhooks/paypal` — and copy the resulting Webhook ID into `PAYPAL_WEBHOOK_ID`.
6. Flip `PAYPAL_VERIFY_WEBHOOKS=1` in production. Leave it `0` in dev so you can drive the flows manually.

### GitHub OAuth (coding agent)

1. https://github.com/settings/developers → New OAuth App.
   - Homepage: `https://animica.org`
   - Callback URL: `<PUBLIC_BASE_URL>/api/integrations/github/callback`
2. Copy the Client ID + Secret into `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET`.
3. Default scopes (`repo,read:user,user:email`) let the agent read private repos and open PRs. For public-only, swap `repo` → `public_repo`.

### GitLab OAuth (coding agent)

1. https://gitlab.com/-/profile/applications → New Application.
   - Redirect URI: `<PUBLIC_BASE_URL>/api/integrations/gitlab/callback`
   - Scopes: `api`, `read_user`, `read_repository`, `write_repository`.
2. Copy values into `GITLAB_OAUTH_CLIENT_ID` / `GITLAB_OAUTH_CLIENT_SECRET`.
3. For a self-hosted instance, set `GITLAB_HOST=https://gitlab.example.com`.

### AI provider

Anything that speaks `/v1/chat/completions` with the OpenAI request
shape — OpenAI, Together.ai, Groq, Anyscale, vLLM, ollama (`--api-key`),
Animica's own AICF gateway, etc. Set `AI_BASE_URL` + `AI_API_KEY` +
`AI_DEFAULT_MODEL`.

## Production deployment

See `deploy/nginx.animica.org.example.conf` and
`deploy/animica-chat-server.example.service`. Sketch:

1. Build:
   ```bash
   pnpm install --prod=false
   pnpm build           # builds server + web
   pnpm prisma:deploy   # applies migrations
   pnpm prisma:seed     # idempotent
   ```
2. Drop the server systemd unit into `/etc/systemd/system/animica-chat-server.service`
   (edit `WorkingDirectory` if the repo lives elsewhere). Stash secrets in
   `/etc/animica/chat.env`. Enable + start.
3. Rsync `web/dist/` → `/var/www/animica-chat/web/`.
4. Symlink the example nginx vhost into `sites-enabled/`, run `nginx -t`,
   then `systemctl reload nginx`. The vhost serves the chat SPA at the
   listed app routes and falls through to the existing Astro marketing
   site for everything else.

## Honest scope notes

This codebase is a real foundation, not a finished SaaS. What's
production-grade vs needs follow-up work:

| Area                                  | State                                                                 |
|---------------------------------------|-----------------------------------------------------------------------|
| Auth (magic link)                     | Wired end-to-end; console mailer in dev, SMTP in prod                 |
| PayPal Subscriptions                  | Create-sub + webhook handler + cancel; needs sandbox creds to exercise |
| Webhook signature verification        | Implemented via PayPal's verify-webhook-signature API; on when `PAYPAL_VERIFY_WEBHOOKS=1` |
| AI provider abstraction               | OpenAI-compatible streaming + tool-calling; works against any compatible gateway |
| Assistant modes                       | 6 modes seeded; system prompts admin-editable                         |
| Chat streaming                        | SSE event stream with per-token deltas, tool-call cards, errors        |
| GitHub + GitLab OAuth                 | Token exchange, encrypted-at-rest storage, /tools UI                  |
| GitHub agent tools                    | list_repos / get_repo / read_file / propose_change (opens **draft PR**) |
| GitLab agent tools                    | list_projects / read_file / propose_change (opens **draft MR**)        |
| Usage gating                          | Free-daily and Pro-monthly caps enforced in middleware                |
| Admin dashboard                       | Users, subs, MRR, usage summary, system-prompt editor                 |
| ChatGPT-grade UX                      | Streaming cursor, markdown + syntax-highlighted code, copy/regenerate buttons, keyboard shortcuts, conversation sidebar with search + rename + delete |
| CLI coding agent                      | Skeleton with login + REPL + one-shot prompt + SSE streaming. Local-write tools (read/write/edit/bash) are **the next thing to land** — see `cli/src/main.ts` |
| Wallet linking, badges, profile pages | Documented in the Prisma schema's deferred list; intentionally not in MVP |
| Image generation, file upload         | Out of scope for the v1 ship                                          |
| Test suite                            | None yet — add `vitest` and per-route happy-path tests before launch  |
| Rate-limit storage                    | In-memory only; switch to Redis before multi-instance deploys         |
| Encryption-at-rest for messages       | Not implemented — Postgres at-rest encryption is the next bar         |

## Where the rest of the spec lives

- Wallet connect (`window.animica`), `.animica` profile names, subscriber badges, user profile pages at `/u/:username`: schema room is reserved in Prisma but the routes/UI are deferred until after the subscription flow has live customers.
- Team accounts, API keys, file-upload chat: an `ApiKey` model exists for the CLI; the rest is documented as deferred so the v1 can ship.

## Repository pointers

- `apps/chat-animica.archive-2026-05-29/` — the prior Next.js Studio app, archived for reference. `studio-animica.service` was stopped + disabled when this app replaced it. Restore command in the nginx vhost comment.
- `apps/animica-chat/deploy/` — example nginx vhost + systemd unit (not installed by build).

Token rotation reminders: the GitHub PAT + PyPI token used earlier in this engineering session are still in the chat transcript. Revoke them.
