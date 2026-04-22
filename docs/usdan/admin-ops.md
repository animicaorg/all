# Admin Operations

Admin API routes require `x-admin-api-key`.

## Common actions

- Set KYC status:
  - `POST /admin/kyc/set-status`
- Add compliance flag:
  - `POST /admin/compliance/flags`
- Publish reserve snapshot:
  - `POST /admin/reserves/publish`
- Monitor queues:
  - `GET /admin/purchases`
  - `GET /admin/redemptions`
  - `GET /admin/webhooks`

All actions should emit `admin_actions` and `audit_logs` records.
