/*
  Animica Terraform: multi-cloud cluster entrypoint

  This file conditionally instantiates exactly one of:
    - modules/gke
    - modules/eks
    - modules/aks

  Then (optionally) wires k8s add-ons (ingress / external-dns / cert-manager)
  via modules/k8s-addons.

  Variables referenced here are defined in variables.tf in this folder.
  See ops/terraform/README.md for tfvars examples.
*/

terraform {
  required_version = ">= 1.5.0"

  # You will still need provider configuration blocks (providers.tf),
  # but we declare the constraints here for clarity.
  required_providers {
    google  = { source = "hashicorp/google",  version = "~> 5.0" }
    aws     = { source = "hashicorp/aws",     version = "~> 5.0" }
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.100" }
    # helm/kubernetes providers are typically configured in providers.tf
    helm        = { source = "hashicorp/helm",        version = "~> 2.12" }
    kubernetes  = { source = "hashicorp/kubernetes",  version = "~> 2.25" }
  }
}

# -------- Cloud selection helpers -------------------------------------------
locals {
  is_gke = var.cloud == "gke"
  is_eks = var.cloud == "eks"
  is_aks = var.cloud == "aks"
}

# -------- GKE cluster (enabled when cloud == "gke") --------------------------
module "gke" {
  count        = local.is_gke ? 1 : 0
  source       = "./modules/gke"

  project_id   = var.project_id
  region       = var.region
  zones        = var.zones
  cluster_name = var.cluster_name
  k8s_version  = var.k8s_version

  # Unified interface expected by the module; shape documented in variables.tf
  node_pools   = var.node_pools
  tags         = var.tags
}

# -------- EKS cluster (enabled when cloud == "eks") --------------------------
module "eks" {
  count        = local.is_eks ? 1 : 0
  source       = "./modules/eks"

  aws_account_id = var.aws_account_id
  region         = var.region
  zones          = var.zones
  cluster_name   = var.cluster_name
  k8s_version    = var.k8s_version

  node_pools     = var.node_pools
  tags           = var.tags
}

# -------- AKS cluster (enabled when cloud == "aks") --------------------------
module "aks" {
  count        = local.is_aks ? 1 : 0
  source       = "./modules/aks"

  azure_subscription_id = var.azure_subscription_id
  region                = var.region
  zones                 = var.zones
  cluster_name          = var.cluster_name
  k8s_version           = var.k8s_version

  node_pools            = var.node_pools
  tags                  = var.tags
}

# -------- Selected cluster outputs (normalize across clouds) -----------------
locals {
  # API server endpoint
  cluster_endpoint = coalesce(
    try(module.gke[0].endpoint, null),
    try(module.eks[0].endpoint, null),
    try(module.aks[0].endpoint, null)
  )

  # Base64-encoded cluster CA cert (PEM)
  ca_cert = coalesce(
    try(module.gke[0].ca_cert, null),
    try(module.eks[0].ca_cert, null),
    try(module.aks[0].ca_cert, null)
  )

  # Optional kubeconfig file path (if module emits one)
  kubeconfig_path = coalesce(
    try(module.gke[0].kubeconfig_path, null),
    try(module.eks[0].kubeconfig_path, null),
    try(module.aks[0].kubeconfig_path, null)
  )
}

# -------- Optional: k8s add-ons (ingress / external-dns / cert-manager) -----
module "k8s_addons" {
  count  = (var.ingress_enabled || var.external_dns_enabled || var.cert_manager_enabled) ? 1 : 0
  source = "./modules/k8s-addons"

  cloud                = var.cloud
  cluster_name         = var.cluster_name
  region               = var.region

  ingress_enabled      = var.ingress_enabled
  external_dns_enabled = var.external_dns_enabled
  cert_manager_enabled = var.cert_manager_enabled

  dns_zone_name        = var.dns_zone_name

  # Connection info (the module can create its own helm/kubernetes providers
  # using these values via provider aliases)
  kube_host                = local.cluster_endpoint
  cluster_ca_certificate   = local.ca_cert

  # Optional inputs the module may accept for auth (provider-specific)
  gcp_project_id           = try(var.project_id, null)
  aws_account_id           = try(var.aws_account_id, null)
  azure_subscription_id    = try(var.azure_subscription_id, null)

  tags = var.tags
}

# -------- Convenience outputs ------------------------------------------------
# (You may also split these into outputs.tf)
output "cloud" {
  description = "Selected cloud provider (gke|eks|aks)."
  value       = var.cloud
}

output "cluster_name" {
  description = "Kubernetes cluster name."
  value       = var.cluster_name
}

output "region" {
  description = "Primary region for the cluster."
  value       = var.region
}

output "kube_endpoint" {
  description = "Kubernetes API server endpoint."
  value       = local.cluster_endpoint
}

output "kube_ca_cert" {
  description = "Base64-encoded cluster CA certificate (PEM)."
  value       = local.ca_cert
  sensitive   = true
}

output "kubeconfig_path" {
  description = "Path to a kubeconfig file emitted by the selected module (if any)."
  value       = local.kubeconfig_path
}

output "ingress_lb_hostname" {
  description = "Ingress load balancer hostname if the addons module enabled ingress."
  value       = try(module.k8s_addons[0].ingress_hostname, null)
}
