# TLS for Animica Devnets & Staging
This guide explains **two supported ways** to run TLS for Animica surfaces (RPC, studio-web, explorer-web, services):

1) **ACME (recommended)** — Automatic Let's Encrypt certificates via `cert-manager` on Kubernetes (HTTP-01 or DNS-01).  
2) **Self-signed (dev only)** — Quick certs for air-gapped or local testing.

> If you already terminate TLS at a CDN/edge proxy (Cloudflare, Fastly, ELB/ALB, etc.), you can keep that. Point it at your cluster's HTTP backends, or use origin certificates. See **External TLS Termination** below.

---

## What surfaces use TLS?
- **HTTP(S) & WS(S)** user-facing endpoints:
  - `studio-web` (static UI)
  - `explorer-web` (static UI)
  - `studio-services` (FastAPI proxy)
  - `rpc` (JSON-RPC + WebSocket)
- **P2P** normally stays on QUIC/TCP with its own crypto (no TLS). Do **not** try to wrap P2P in TLS.

---

## Option A — ACME via cert-manager (Kubernetes)
This is the default for internet-facing devnets and staging. It auto-provisions and renews certificates.

### Prerequisites
- Deployed K8s base (see `ops/k8s/README.md`) and chosen overlay (`devnet` or `prod`).
- Working ingress controller (nginx, traefik or equivalent).
- Public DNS records pointing to your ingress LB IP/hostname (A/AAAA/CNAME).
- Port **80** reachable on the internet for **HTTP-01** (or ability to set DNS TXT for **DNS-01**).

### Files in this repo
- `ops/k8s/ingress/ingress.yaml` — Ingress for studio/explorer/RPC.
- `ops/k8s/ingress/external-dns.yaml` — Optional DNS automation.
- `ops/k8s/ingress/cert-manager.yaml` — **ClusterIssuer/Issuer** for Let's Encrypt (staging & prod).
- Overlays patch ingress hosts:
  - `ops/k8s/overlays/devnet/patches/ingress-hosts.yaml`

### Install cert-manager
```bash
# 1) Install cert-manager CRDs & controller (official quickstart)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.2/cert-manager.yaml

# 2) Wait for pods to be ready
kubectl -n cert-manager rollout status deploy/cert-manager
kubectl -n cert-manager rollout status deploy/cert-manager-webhook
kubectl -n cert-manager rollout status deploy/cert-manager-cainjector

Configure ClusterIssuer

Edit ops/k8s/ingress/cert-manager.yaml to set:
	•	Contact email (Let’s Encrypt account)
	•	Solver: http01 (default) or dns01 (required for wildcard *.example.com)

Apply:

kubectl apply -f ops/k8s/ingress/cert-manager.yaml

Tip: Start with staging issuer (rate-limit free), validate, then switch to prod.

Deploy ingress with TLS annotations

Confirm hosts in ops/k8s/overlays/devnet/patches/ingress-hosts.yaml and apply overlay:

kubectl apply -k ops/k8s/overlays/devnet

The Ingress should reference:

metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod   # or letsencrypt-staging
spec:
  tls:
    - hosts:
        - studio.example.com
        - explorer.example.com
        - rpc.example.com
      secretName: animica-tls

Verify certificate issuance

kubectl get certificate -A
kubectl describe certificate animica-tls -n animica-devnet
kubectl get secret animica-tls -n animica-devnet

A successful issuance creates tls.crt and tls.key in the secret. Ingress will present it.

When to use DNS-01
	•	You need wildcards (e.g., *.devnet.example.com)
	•	Port 80 is blocked or LB cannot route /.well-known/acme-challenge/*
	•	Use a DNS provider with cert-manager solver support (Cloudflare, Route53, etc.)

Switch solver in the ClusterIssuer to dns01, add credentials as a Kubernetes secret, and re-apply.

Renewal

cert-manager auto-renews before expiry. You can observe events:

kubectl describe certificate animica-tls -n animica-devnet


⸻

Option B — Self-signed certificates (local/dev only)

Useful for air-gapped tests or when no public DNS is available.

Generate with OpenSSL

mkdir -p ops/tls/selfsigned
openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
  -keyout ops/tls/selfsigned/tls.key \
  -out ops/tls/selfsigned/tls.crt \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

(Optional) Generate with mkcert (auto trust on dev machines)

# Install mkcert & a local CA, then:
mkcert -install
mkcert -key-file ops/tls/selfsigned/tls.key \
      -cert-file ops/tls/selfsigned/tls.crt \
      localhost 127.0.0.1 ::1

Use in Kubernetes

Create a TLS secret and point Ingress to it:

kubectl -n animica-devnet create secret tls animica-selfsigned \
  --cert=ops/tls/selfsigned/tls.crt \
  --key=ops/tls/selfsigned/tls.key

Update ingress.yaml:

metadata:
  annotations:
    cert-manager.io/cluster-issuer: ""   # remove or leave absent
spec:
  tls:
    - hosts: ["localhost"]
      secretName: animica-selfsigned

Use in Docker Compose (local)
	•	Mount tls.crt/tls.key into the nginx container and reference in ops/docker/nginx/default.conf.
	•	Or run a local reverse proxy (Caddy, Traefik) that watches cert files.

Browsers will warn for self-signed unless you trust the CA (mkcert helps for local dev).

⸻

External TLS Termination (CDN/ELB/Cloud proxy)

If you terminate TLS at the edge:
	•	Keep Ingress HTTP (no TLS); allow only from the proxy’s IPs if possible.
	•	Configure HSTS at the edge; enable WebSocket proxying for /ws endpoints.
	•	For RPC methods: ensure CORS and large body/timeout limits match ops/docker/nginx/default.conf.

⸻

Security & Hardening Notes
	•	Prefer ECDSA P-256 or RSA 2048/3072 keys.
	•	Enforce TLSv1.2+ (ideally TLSv1.3). Use modern ciphers. Example nginx snippet:

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers HIGH:!aNULL:!MD5;
ssl_ecdh_curve X25519:P-256;
ssl_prefer_server_ciphers on;
ssl_session_cache shared:SSL:10m;


	•	Enable OCSP stapling where supported.
	•	Protect private keys: limit access to ingress-controller namespace; restrict RBAC on secrets.
	•	Use Let’s Encrypt staging to avoid production rate limits during testing.

⸻

Troubleshooting

Symptom	Likely Cause	Fix
Certificate is not ready	HTTP-01 path not reachable	Check DNS A/AAAA, firewall for :80, Ingress class, path /.well-known/acme-challenge/…
ACME solver pod CrashLoop	Missing ingress class or bad annotations	Match ingress class to your controller (e.g., kubernetes.io/ingress.class: nginx)
Wildcard fails	Using HTTP-01	Switch to DNS-01 solver
Issuer errors: rateLimited	Hitting prod limits	Use letsencrypt-staging until stable
Browser warns self-signed	Not trusted	Use mkcert and import CA; for teams, distribute CA or move to ACME
secret not found in Ingress	Wrong secretName	Ensure Certificate and Secret names match; same namespace as Ingress
WebSockets over TLS fail	Proxy not forwarding upgrade	Ensure Connection: upgrade and Upgrade: websocket are passed through (nginx/traefik config)

Quick checks:

# See events & conditions on Certificate
kubectl describe certificate animica-tls -n animica-devnet

# Inspect ACME orders/challenges
kubectl get orders,orders.acme.cert-manager.io -A
kubectl describe challenge -A

# Check presented cert from outside
echo | openssl s_client -connect studio.example.com:443 -servername studio.example.com 2>/dev/null | openssl x509 -noout -dates -issuer -subject


⸻

Migration: Staging → Production
	1.	Point DNS to ingress; verify 80/443 reachability.
	2.	Deploy with letsencrypt-staging. Confirm green padlock (staging is trusted by few clients, but issuance should succeed).
	3.	Switch issuer in Ingress to letsencrypt-prod. Re-apply and verify.
	4.	Enable HSTS and stricter ciphers after stable.

⸻

Using repo secrets manifests (optional)
	•	ops/k8s/secrets/tls.yaml demonstrates a static TLS Secret pattern. For ACME you normally don’t need this — cert-manager manages the secret.
	•	Never commit private keys. If you must use static secrets, seal them (SOPS/SealedSecrets/External Secrets).

⸻

Compose-only ACME (alternative)

If you don’t run K8s:
	•	Use Traefik or Caddy in front of the dockerized services; both support automatic ACME with HTTP-01.
	•	Ensure the reverse proxy can bind :80 and that DNS points to the host.
	•	Map backends to the same paths/hosts used by ops/docker/nginx/default.conf.

⸻

Reference checklists

ACME (HTTP-01)
	•	DNS A/AAAA → ingress LB public IP
	•	Port 80 open to the world
	•	ClusterIssuer applied; email set
	•	Ingress has cert-manager.io/cluster-issuer annotation
	•	Single secretName shared across hosts or per-host secrets
	•	Certificate Ready=True

ACME (DNS-01)
	•	DNS provider supported by cert-manager
	•	API credentials stored as K8s Secret
	•	Solver configured in ClusterIssuer
	•	TXT challenges appear in DNS during issuance

Self-signed
	•	Generated tls.crt/tls.key
	•	K8s Secret created
	•	Ingress references the secret (no cert-manager annotation)
	•	Local CA trusted on dev machines (if mkcert)

⸻

Related files in this repo
	•	ops/k8s/ingress/cert-manager.yaml — Issuers (staging/prod)
	•	ops/k8s/ingress/ingress.yaml — Ingress template
	•	ops/k8s/overlays/devnet/patches/ingress-hosts.yaml — Hostnames
	•	ops/docker/nginx/default.conf — TLS & proxy defaults for static sites
	•	ops/docker/docker-compose.prod-example.yml — Example internet-facing stack

⸻

FAQ

Q: Can I put RPC behind the same cert as studio/explorer?
A: Yes. Add the RPC host to the same tls.hosts array and route by Host header.

Q: Do I need TLS for WebSockets?
A: For browser clients on HTTPS pages, you must use wss:// (TLS). Ensure proxy passes Upgrade headers.

Q: How are renewals handled?
A: cert-manager renews automatically ~30 days before expiry; the Ingress reloads the secret.

⸻

Happy shipping! If you hit snags, start with staging ACME + a single host, verify HTTP-01 reachability, then expand.
