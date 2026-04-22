# Auth

- Developer/provider auth supports email/password and OAuth callback scaffolding.
- Session token returned by `/auth/signup` and `/auth/login`.
- Pass session token in `x-session-token` for dashboard operations.
- Wallet linking is separate from login and used for on-chain ANM operations.
