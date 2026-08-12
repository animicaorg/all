# Animica Python Cloud — the execution sandbox image.
#
# This image runs UNTRUSTED developer code. It contains no Animica source, no credentials, no
# wallet material and no network clients: every privileged operation is an RPC to the host
# broker over the runner's private stdio channel.
#
# Build:  ./sandbox/build-image.sh
# Runtime hardening is applied by the caller (lib/cloud/sandbox.ts), not baked in here:
#   --network none --read-only --cap-drop ALL --security-opt no-new-privileges
#   --memory --cpus --pids-limit --user 10001:10001 --tmpfs /tmp
FROM python:3.12-slim

# The curated package set (§27). Deliberately excludes HTTP clients (requests/httpx/urllib3
# extras): the sandbox has no network, so the only outbound path is animica.http.fetch, which
# the host mediates and SSRF-guards. Anything not listed here simply is not importable, and
# `pip install` at runtime is impossible by construction because there is no network.
RUN pip install --no-cache-dir \
      numpy==2.1.3 \
      pandas==2.2.3 \
      pyyaml==6.0.2 \
      python-dateutil==2.9.0.post0 \
      orjson==3.10.12 \
      jinja2==3.1.4 \
      markdown==3.7 \
      beautifulsoup4==4.12.3 \
      pillow==11.0.0 \
      cryptography==44.0.0 \
      pydantic==2.10.3 \
 && find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && rm -rf /root/.cache

# Unprivileged, no shell, no home to write to. The rootfs is mounted read-only at runtime;
# /tmp is a small tmpfs supplied by the caller.
RUN groupadd -g 10001 anmrun \
 && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin anmrun \
 && mkdir -p /app/code \
 && chown -R root:root /app \
 && chmod -R 0555 /app

COPY runner.py /app/runner.py
RUN chmod 0444 /app/runner.py

# Strip the setuid/setgid bits from everything in the image so no-new-privileges has nothing
# to bite on even if a future base-image change adds one.
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

USER 10001:10001
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_INPUT=1 \
    HOME=/tmp

ENTRYPOINT ["python3", "-I", "-B", "/app/runner.py"]
