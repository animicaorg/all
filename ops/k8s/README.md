# Animica — Kubernetes Ops Guide

This doc covers **cluster prerequisites**, **Ingress + TLS** with cert-manager, and **ExternalDNS** so your devnet (node, miner, explorer, studio) becomes reachable on public hostnames with automatic certificates.

It assumes you’ve looked at `ops/.env.example` and (optionally) run `ops/scripts/env_check.sh`.

---

## 1) Prerequisites

**Cluster**
- Kubernetes **v1.27+** (tested on 1.27–1.30).
- Cloud LB support (or bare-metal LB like MetalLB).
- Default `StorageClass` (RWO) for small SQLite volumes; RWX optional for shared caches.
- Nodes with Docker/Containerd that can pull public images.

**CLI tooling**
- `kubectl` 1.27+ and `helm` 3.11+ installed locally.
- DNS provider credentials (Route53 / Cloud DNS / Cloudflare / Azure DNS, etc.) for ExternalDNS.

**Networking**
- Outbound internet access for pods (to pull images and, if used, AICF demos).
- Ingress Controller reachable from the internet (or via your LB).

**Namespaces (convention)**
- `animica-system` — ingress-nginx, cert-manager, external-dns.
- `animica` — Animica apps (node, miner, explorer, studio-services, UIs).
- `observability` — Prometheus, Grafana, Loki/Tempo/Promtail (optional).

Create them:
```bash
kubectl create namespace animica-system || true
kubectl create namespace animica || true
kubectl create namespace observability || true


⸻

2) Domain & Environment

Populate a real .env (based on ops/.env.example) and export in your shell:

export DOMAIN_BASE=devnet.example.com        # parent DNS zone you control
export TLS_EMAIL=ops@devnet.example.com      # ACME email for Let's Encrypt
export CHAIN_ID=1                            # chain id exposed by node
# Optional: ExternalDNS specifics (examples below per provider)

Recommended hostnames:
	•	rpc.${DOMAIN_BASE} — JSON-RPC & WS
	•	explorer.${DOMAIN_BASE} — explorer API/UI
	•	studio.${DOMAIN_BASE} — studio-web
	•	services.${DOMAIN_BASE} — studio-services REST (deploy/verify/faucet)
	•	da.${DOMAIN_BASE} — DA endpoints (if exposed)

Tip: ops/scripts/render_ingress_values.py can help produce values from .env.

⸻

3) Install Ingress Controller

We use ingress-nginx (stable & ubiquitous), installed in animica-system.

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace animica-system \
  --set controller.replicaCount=2 \
  --set controller.metrics.enabled=true \
  --set controller.ingressClassResource.name=nginx \
  --set controller.ingressClass=nginx

Wait for an external address:

kubectl -n animica-system get svc ingress-nginx-controller

Point your DNS zone apex (or the specific hostnames later managed by ExternalDNS) at this LB if you’re not using ExternalDNS. If you are using ExternalDNS, keep reading.

⸻

4) TLS with cert-manager (Let’s Encrypt)

Install CRDs + chart:

helm repo add jetstack https://charts.jetstack.io
helm repo update

helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace animica-system \
  --set installCRDs=true

Create a ClusterIssuer for HTTP-01 ACME (Let’s Encrypt):

# save as issuers-letsencrypt.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt
spec:
  acme:
    email: REPLACE_WITH_${TLS_EMAIL}
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: acme-account-key
    solvers:
      - http01:
          ingress:
            class: nginx

Apply:

sed "s/REPLACE_WITH_${TLS_EMAIL}/${TLS_EMAIL}/" issuers-letsencrypt.yaml | kubectl apply -f -

You’ll reference this issuer on each Ingress via cert-manager.io/cluster-issuer: "letsencrypt".

⸻

5) ExternalDNS (automated DNS records)

Choose the provider that manages DOMAIN_BASE and install one of the following snippets.

Ensure your cloud account/role has write access to the zone.

a) Route53 (AWS)

helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

helm upgrade --install external-dns bitnami/external-dns \
  --namespace animica-system \
  --set provider=aws \
  --set policy=upsert-only \
  --set registry=txt \
  --set txtOwnerId=animica-devnet \
  --set domainFilters={"${DOMAIN_BASE}"} \
  --set aws.zoneType=public \
  --set serviceAccount.create=true \
  --set serviceAccount.name=external-dns \
  --set podSecurityContext.fsGroup=65534

Provide AWS creds via IRSA (recommended) or a secret (aws_access_key_id/aws_secret_access_key).

b) Google Cloud DNS

helm upgrade --install external-dns bitnami/external-dns \
  --namespace animica-system \
  --set provider=google \
  --set google.project=YOUR_GCP_PROJECT \
  --set policy=upsert-only \
  --set registry=txt \
  --set txtOwnerId=animica-devnet \
  --set domainFilters={"${DOMAIN_BASE}"} \
  --set serviceAccount.create=true \
  --set serviceAccount.name=external-dns

Mount a Service Account JSON key as a secret and reference it via chart values if not using Workload Identity.

c) Cloudflare

helm upgrade --install external-dns bitnami/external-dns \
  --namespace animica-system \
  --set provider=cloudflare \
  --set cloudflare.apiToken=YOUR_CF_API_TOKEN \
  --set policy=upsert-only \
  --set registry=txt \
  --set txtOwnerId=animica-devnet \
  --set domainFilters={"${DOMAIN_BASE}"}

Verify controller logs: kubectl -n animica-system logs deploy/external-dns

⸻

6) Example Ingresses

Below routes four public hosts to their services. Replace images/service names according to your manifests or Helm values.

# ingress-animica.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: animica-public
  namespace: animica
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt
    external-dns.alpha.kubernetes.io/ttl: "60"
spec:
  tls:
    - hosts: [ "rpc.${DOMAIN_BASE}", "explorer.${DOMAIN_BASE}", "studio.${DOMAIN_BASE}", "services.${DOMAIN_BASE}" ]
      secretName: animica-tls
  rules:
    - host: rpc.${DOMAIN_BASE}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: rpc                      # your FastAPI RPC Service
                port:
                  number: 8080
    - host: explorer.${DOMAIN_BASE}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: explorer-web             # your explorer UI Service
                port:
                  number: 80
    - host: studio.${DOMAIN_BASE}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: studio-web               # studio UI Service
                port:
                  number: 80
    - host: services.${DOMAIN_BASE}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: studio-services          # FastAPI proxy Service
                port:
                  number: 8080

Apply:

envsubst < ingress-animica.yaml | kubectl apply -f -

Check:

kubectl -n animica get ing animica-public
kubectl -n animica describe ing animica-public

Once the Ingress is ready, ExternalDNS will create A/AAAA records; cert-manager will solve HTTP-01 and issue a cert into animica/animica-tls.

⸻

7) Secrets & Config
	•	Node, miner, services and explorer read their config from ConfigMaps/Secrets mirroring the Docker env files in ops/docker/config/* and service env in ops/docker/config/services.env.
	•	Example:

kubectl -n animica create secret generic studio-services-env --from-env-file=ops/docker/config/services.env


	•	For studio-services with faucet enabled, store the faucet hot key only in a Secret.

⸻

8) Storage

For devnet, emptyDir or small PVCs suffice:
	•	Node DB: 1–5 Gi
	•	DA store (optional): 5–20 Gi depending on tests
	•	AICF queue/DB (optional): 1–5 Gi
Use ReadWriteOnce PVCs bound to a single pod.

⸻

9) Observability (optional but recommended)

Use the compose stack’s Prometheus/Grafana/Loki equivalents or your platform’s managed stack.

Helm quickstart (community charts):

helm repo add grafana https://grafana.github.io/helm-charts
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install kube-prom prometheus-community/kube-prometheus-stack \
  --namespace observability --create-namespace

# Loki + Promtail (if not using a managed solution)
helm upgrade --install loki grafana/loki \
  --namespace observability --set loki.auth_enabled=false
helm upgrade --install promtail grafana/promtail \
  --namespace observability --set config.lokiAddress=http://loki.observability.svc.cluster.local:3100/loki/api/v1/push

Import dashboards equivalent to ops/docker/config/grafana/dashboards/*.json.

⸻

10) Network Policies (optional)

Lock down namespaces so only:
	•	Ingress controller can reach app Services on HTTP/WS ports.
	•	Apps can reach each other as needed (RPC ↔ miner, explorer → RPC, studio-services → RPC).
	•	Prometheus can scrape /metrics.

Start permissive and tighten iteratively.

⸻

11) Troubleshooting
	•	DNS not created: check ExternalDNS logs; confirm domainFilters matches DOMAIN_BASE.
	•	ACME fails: kubectl -n animica describe certificate animica-tls and cert-manager logs. Ensure HTTP-01 path is reachable (no conflicting Ingress).
	•	Ingress 404: verify ingressClass is nginx and Services/ports exist.
	•	WS issues: add NGINX annotations for websockets if your RPC uses them:

nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
nginx.ingress.kubernetes.io/websocket-services: "rpc"



Useful commands:

kubectl get events -A --sort-by=.lastTimestamp | tail -n 50
kubectl -n animica-system logs -l app.kubernetes.io/name=ingress-nginx -f
kubectl -n animica-system logs deploy/cert-manager -f
kubectl -n animica-system logs deploy/external-dns -f


⸻

12) Cleanup

kubectl delete ingress -n animica animica-public || true
helm -n animica-system uninstall external-dns cert-manager ingress-nginx || true
kubectl delete ns animica animica-system observability


⸻

Appendix: Minimal Service stubs

If you’re hand-crafting Services, ensure they exist before applying the Ingress.

apiVersion: v1
kind: Service
metadata:
  name: rpc
  namespace: animica
spec:
  selector: { app: rpc }
  ports:
    - name: http
      port: 8080
      targetPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: explorer-web
  namespace: animica
spec:
  selector: { app: explorer-web }
  ports:
    - name: http
      port: 80
      targetPort: 80

For full manifests or Helm values: mirror the configs from ops/docker/config/*.toml/.env into ConfigMaps/Secrets and mount via env/volumes.

