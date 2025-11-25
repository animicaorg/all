# ------------------------------------------------------------------------------
# Animica Promtail (optional) — log shipper with label enrichment
#
# Base: grafana/promtail (small, statically-linked)
# Adds:
#   - Env-driven label enrichment (job/service/env/version/chain/region/zone)
#   - Config renderer (merges extra scrape configs from /etc/promtail/scrape.d)
#   - Healthcheck on /ready
# ------------------------------------------------------------------------------

ARG PROMTAIL_VERSION=2.9.0
FROM grafana/promtail:${PROMTAIL_VERSION}

# Defaults (override at runtime)
ENV LOKI_URL="http://loki:3100/loki/api/v1/push" \
    PROMTAIL_HTTP_PORT=9080 \
    LOG_LEVEL=info \
    # Optional enrichment labels:
    JOB=promtail \
    SERVICE=animica \
    ENVIRONMENT=devnet \
    VERSION=local \
    CHAIN_ID=1 \
    REGION=local \
    ZONE=local-a

USER root
# Prepare writable dirs; promtail image usually runs as UID 10001
RUN mkdir -p /etc/promtail/scrape.d /var/log/promtail \
 && chown -R 10001:10001 /etc/promtail /var/log/promtail

# Tiny entry wrapper renders config.yml then execs promtail
# - You can mount extra YAML snippets at /etc/promtail/scrape.d/*.yml to extend scrape_configs
ADD --chown=10001:10001 . /app 2>/dev/null || true

RUN printf '%s\n' '#!/usr/bin/env sh' \
'set -eu' \
': "${PROMTAIL_HTTP_PORT:=9080}"' \
': "${LOKI_URL:=http://loki:3100/loki/api/v1/push}"' \
': "${LOG_LEVEL:=info}"' \
'OUT=/etc/promtail/config.yml' \
'' \
'echo ">> Rendering $OUT with label enrichment..."' \
'{' \
'  echo "server:"' \
'  echo "  http_listen_port: ${PROMTAIL_HTTP_PORT}"' \
'  echo "  grpc_listen_port: 0"' \
'' \
'  echo "positions:"' \
'  echo "  filename: /var/log/promtail/positions.yaml"' \
'' \
'  echo "clients:"' \
'  echo "  - url: ${LOKI_URL}"' \
'  echo "    batchwait: 1s"' \
'  echo "    batchsize: 1048576"' \
'  echo "    timeout: 10s"' \
'  echo "    external_labels:"' \
'  [ -n "${JOB:-}" ]          && echo "      job: \"${JOB}\""' \
'  [ -n "${SERVICE:-}" ]      && echo "      service: \"${SERVICE}\""' \
'  [ -n "${ENVIRONMENT:-}" ]  && echo "      env: \"${ENVIRONMENT}\""' \
'  [ -n "${VERSION:-}" ]      && echo "      version: \"${VERSION}\""' \
'  [ -n "${CHAIN_ID:-}" ]     && echo "      chain_id: \"${CHAIN_ID}\""' \
'  [ -n "${REGION:-}" ]       && echo "      region: \"${REGION}\""' \
'  [ -n "${ZONE:-}" ]         && echo "      zone: \"${ZONE}\""' \
'' \
'  echo "scrape_configs:"' \
'  echo "  - job_name: system"' \
'  echo "    static_configs:"' \
'  echo "      - targets: [localhost]"' \
'  echo "        labels:"' \
'  echo "          job: varlogs"' \
'  echo "          __path__: /var/log/*.log"' \
'' \
'  echo "  - job_name: containers"' \
'  echo "    static_configs:"' \
'  echo "      - targets: [localhost]"' \
'  echo "        labels:"' \
'  echo "          job: containers"' \
'  echo "          __path__: /var/log/containers/*/*.log"' \
'  echo "    pipeline_stages:"' \
'  echo "      - docker: {}"' \
'  echo "      - labeldrop:"' \
'  echo "          - filename"' \
'  echo "          - stream"' \
'} > "${OUT}"' \
'' \
'# Append any additional scrape snippets' \
'if ls /etc/promtail/scrape.d/*.yml >/dev/null 2>&1; then' \
'  echo ">> Merging extra scrape configs from /etc/promtail/scrape.d/*.yml"' \
'  for f in /etc/promtail/scrape.d/*.yml; do' \
'    echo "" >> "${OUT}"' \
'    echo "# --- include: $f ---" >> "${OUT}"' \
'    cat "$f" >> "${OUT}"' \
'  done' \
'fi' \
'' \
'echo ">> Starting promtail..."' \
'exec /usr/bin/promtail -config.file="${OUT}" -log.level="${LOG_LEVEL}"' \
> /usr/local/bin/start-promtail && chmod +x /usr/local/bin/start-promtail

# Drop back to the default non-root user from base image
USER 10001:10001

EXPOSE 9080

# Healthcheck against /ready
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${PROMTAIL_HTTP_PORT:-9080}/ready" || exit 1

# OCI labels
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-promtail" \
      org.opencontainers.image.description="Promtail with env-driven label enrichment for Animica" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENTRYPOINT ["/usr/local/bin/start-promtail"]
