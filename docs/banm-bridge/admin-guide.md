# BANM Admin Guide

## Login

Use seeded credentials in `/api/v1/admin/login` from the admin web app.

## Dashboards

- Order table with status/direction filters
- Selected order detail and event timeline
- Solvency metrics
- Pause controls

## Operator Actions

- Retry failed or stuck settlement: `POST /api/v1/admin/orders/{orderId}/retry`
- Move order to manual review: `POST /api/v1/admin/orders/{orderId}/manual-review?reason=...`
- Pause/unpause bridge directions via pause endpoints

## Review Checklist

Before retrying settlement:

1. Verify order binding fields.
2. Verify deposit tx hash and confirmations.
3. Verify no prior successful settlement exists.
4. Verify reserve sufficiency for reverse release.

## Role Guidance

- `viewer`: read-only.
- `operator`: retry/manual review + directional pause.
- `admin`: full controls including global pause and user provisioning.

