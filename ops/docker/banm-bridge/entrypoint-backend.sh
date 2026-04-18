#!/usr/bin/env sh
set -eu

export PYTHONPATH=/app/python

python -m animica.bridge_banm.migrate upgrade head

if [ -n "${BANM_ADMIN_USERNAME:-}" ] && [ -n "${BANM_ADMIN_PASSWORD:-}" ]; then
  python -m animica.bridge_banm.scripts.seed_admin \
    --username "${BANM_ADMIN_USERNAME}" \
    --password "${BANM_ADMIN_PASSWORD}" \
    --role "${BANM_ADMIN_ROLE:-admin}"
fi

exec python -m animica.bridge_banm

