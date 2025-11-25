# Terraform: one-shot Kubernetes cluster (GKE / EKS / AKS)

This optional IaC pack creates a production-grade Kubernetes cluster for the Animica **devnet** or **prod** environments on your cloud of choice and then leaves the Helm/Kustomize deployment to the files under `ops/k8s` and `ops/helm`.

> You can run devnet locally with Docker Compose and skip this entirely. Use Terraform only when you want a managed k8s control plane with cloud LB/DNS/TLS.

---

## What this does

- Creates/updates a managed Kubernetes cluster:
  - **GKE** (Google Cloud), **EKS** (AWS) or **AKS** (Azure)
- Provisions basic infra:
  - VPC/VNet + subnets, NAT / egress
  - IAM roles / service principals
  - Node pools (on-demand + optional spot/preemptible)
  - Default StorageClass (SSD where available)
- Optional addons (toggle via vars):
  - Ingress controller (NGINX or cloud L7)
  - `external-dns` wired to your DNS zone (Route53 / Cloud DNS / Azure DNS)
  - `cert-manager` (Let’s Encrypt HTTP-01)
  - Observability bootstrap namespace (Prometheus/Grafana installs are handled by `ops/k8s/observability/*`)

Terraform **does not** deploy the Animica services – after the cluster is up, use:
- Kustomize overlays in `ops/k8s/overlays/*`, **or**
- Helm chart in `ops/helm/animica-devnet`

---

## Repository layout (expected)

ops/terraform/
main.tf                # chooses one of gke/eks/aks modules based on provider flags
providers.tf           # required providers + auth blocks
variables.tf           # input variables (see below)
outputs.tf             # kubeconfig path, cluster name, dns records, etc.
backend.hcl.example    # template for remote state backend config
envs/
devnet.tfvars        # small, cost-effective defaults
prod.tfvars          # larger node pools, multi-AZ
modules/
gke/                 # opinionated GKE module
eks/                 # opinionated EKS module
aks/                 # opinionated AKS module
k8s-addons/          # ingress / external-dns / cert-manager (optional)

> Only this README is checked in by default. If you want the full modules, scaffold them from the examples below or your internal module registry.

---

## Prerequisites

- **Terraform** ≥ 1.5
- Cloud CLIs authenticated and configured:
  - GCP: `gcloud auth application-default login` and billing/project set
  - AWS: `aws configure` (Access key or SSO) with permissions for VPC/EKS/IAM
  - Azure: `az login` with subscription selected
- A DNS zone you control (optional but recommended) if enabling `external_dns_enabled`
- A remote state bucket (S3 / GCS / Azure Storage) and a lock table where applicable

---

## Remote state (recommended)

Copy and edit:

```hcl
# backend.hcl.example
bucket         = "your-tf-state-bucket"     # GCS: bucket / AWS: S3 bucket / Azure: storage_account_container
prefix         = "animica/terraform"
region         = "us-east-1"                # if S3
# dynamodb_table = "tf-locks"              # if S3 with locking
# impersonate_service_account = "tf@project.iam.gserviceaccount.com" # GCS

Init with:

terraform init -backend-config=backend.hcl.example


⸻

Quickstart
	1.	Pick a cloud and set provider flags in tfvars:

# envs/devnet.tfvars
cloud               = "gke"         # one of: gke|eks|aks
project_id          = "my-gcp-project"      # or aws_account_id / azure_subscription_id
region              = "us-central1"         # EKS example: "us-east-1", AKS: "eastus"
zones               = ["us-central1-a","us-central1-b"]   # multi-AZ recommended
cluster_name        = "animica-devnet"
k8s_version         = "1.29"
ingress_enabled     = true
external_dns_enabled= true
cert_manager_enabled= true
dns_zone_name       = "dev.example.com"     # existing zone
node_pools = {
  default = {
    machine_type = "e2-standard-4"          # EKS: instance_type, AKS: vm_size
    min = 1
    max = 3
    desired = 2
    spot = false
    labels = { role = "general" }
    taints = []
    disk_gb = 100
  }
  spot = {
    machine_type = "e2-standard-8"
    min = 0
    max = 5
    desired = 0
    spot = true
    labels = { role = "workers" }
    taints = []
    disk_gb = 100
  }
}

	2.	Plan & apply:

cd ops/terraform
terraform init -backend-config=backend.hcl.example
terraform plan  -var-file=envs/devnet.tfvars
terraform apply -var-file=envs/devnet.tfvars

	3.	Fetch kubeconfig (provider-specific helpers):

	•	GKE: gcloud container clusters get-credentials animica-devnet --region us-central1 --project my-gcp-project
	•	EKS: aws eks update-kubeconfig --name animica-devnet --region us-east-1
	•	AKS: az aks get-credentials --name animica-devnet --resource-group <rg>

	4.	Verify:

kubectl get nodes -o wide
kubectl get storageclass

	5.	Deploy Animica stack:

	•	Kustomize:

kubectl apply -k ops/k8s/overlays/devnet


	•	Helm:

helm upgrade --install animica ops/helm/animica-devnet \
  -f ops/helm/animica-devnet/values.yaml



⸻

Key variables (excerpt)

Variable	Type	Default	Description
cloud	string	n/a	gke | eks | aks
project_id / aws_account_id / azure_subscription_id	string	n/a	Cloud account identifier
region	string	n/a	Primary region
zones	list(string)	[]	Availability zones (recommended ≥2)
cluster_name	string	"animica-devnet"	Cluster name
k8s_version	string	provider default	Kubernetes version
node_pools	map(object)	n/a	See example above
ingress_enabled	bool	true	Install ingress controller module
external_dns_enabled	bool	false	Manage DNS records from ingresses
cert_manager_enabled	bool	false	Install cert-manager + ClusterIssuer
dns_zone_name	string	“”	Existing DNS zone (required if external-dns)

Full schema lives in variables.tf in your implementation.

⸻

Outputs (common)
	•	cluster_name
	•	region
	•	kubeconfig_path (if the module emits one)
	•	ingress_lb_hostname (when available)
	•	dns_zone_id (if external-dns is enabled)

⸻

Costs & sizing tips
	•	Prefer a small on-demand pool for system / critical workloads and a scale-to-zero spot pool for miners and stateless workers.
	•	Use SSD storage classes only where needed (DBs). Logs/metrics TSDB can use standard persistent disks.
	•	Set HPA/Cluster Autoscaler limits to prevent accidental scale-outs during load tests.

⸻

Troubleshooting
	•	Auth/IAM: ensure your user/role can create VPC, IAM roles, and managed k8s resources.
	•	Quotas: enabling a cluster often needs extra CPU, IPs, and LB quotas.
	•	APIs not enabled (GCP): gcloud services enable container.googleapis.com compute.googleapis.com
	•	EKS OIDC/IAM: ensure the cluster has IAM OIDC provider and node role trust relationships.
	•	AKS resource group: Terraform usually creates a managed RG; do not delete it manually.

⸻

Destroy

Danger: this deletes the cluster and any stateful PVCs not protected by retention policies.

terraform destroy -var-file=envs/devnet.tfvars

Back up persistent volumes first (DB snapshots, artifact buckets).

⸻

Security notes
	•	Least-privilege your Terraform runner (separate service account/role).
	•	Keep remote state encrypted and access-controlled.
	•	Rotate cloud credentials regularly; prefer short-lived tokens (Workload Identity / IRSA / Managed Identity).
	•	If exposing RPC over the public internet, restrict by IP and enforce CORS and rate limits (already configured in ops/k8s and Helm values).

⸻

Minimal providers.tf sketch (example: GKE)

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.25" }
    helm = { source = "hashicorp/helm", version = "~> 2.12" }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "kubernetes" {
  host                   = module.gke.endpoint
  cluster_ca_certificate = base64decode(module.gke.ca_cert)
  token                  = data.google_client_config.default.access_token
}

provider "helm" {
  kubernetes {
    host                   = module.gke.endpoint
    cluster_ca_certificate = base64decode(module.gke.ca_cert)
    token                  = data.google_client_config.default.access_token
  }
}

data "google_client_config" "default" {}

Use analogous provider blocks for EKS (aws, kubernetes, helm) and AKS (azurerm, kubernetes, helm).

⸻

Next steps
	1.	Stand up the cluster with Terraform.
	2.	Apply ops/k8s/overlays/devnet or install ops/helm/animica-devnet.
	3.	Run ops/scripts/smoke_devnet.sh to confirm: RPC healthy, one block mined, dashboards reachable.
	4.	(Optional) Point your DNS to the provisioned ingress LB and enable TLS via cert-manager.

