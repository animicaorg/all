/*
  Terraform outputs for Animica devnet clusters (multi-cloud).

  These outputs are intentionally defensive: they use try(...) to work whether
  you're targeting GKE, EKS, or AKS. If a particular module/key isn't present,
  the value resolves to null/[] instead of erroring.

  Expected module names (from main.tf):
    - module.gke
    - module.eks
    - module.aks
*/

# ------------ High-level cluster identity ------------------------------------

output "cluster" {
  description = "Top-level cluster identity (cloud, name, region, zones)."
  value = {
    cloud        = var.cloud
    cluster_name = var.cluster_name
    region       = var.region
    zones        = var.zones
    k8s_version  = var.k8s_version
  }
}

# ------------ Control-plane details ------------------------------------------

output "control_plane" {
  description = "Control-plane endpoint and CA (if exposed by the module)."
  value = {
    endpoint               = try(module.gke.endpoint, try(module.eks.endpoint, try(module.aks.endpoint, null)))
    cluster_ca_certificate = try(module.gke.cluster_ca_certificate, try(module.eks.cluster_ca_certificate, try(module.aks.cluster_ca_certificate, null)))
    oidc_issuer_url        = try(module.gke.oidc_issuer_url, try(module.eks.oidc_issuer_url, try(module.aks.oidc_issuer_url, null)))
  }
}

# Some modules expose a rendered kubeconfig blob. Mark sensitive if present.
output "kubeconfig" {
  description = "Rendered kubeconfig content (if the selected cloud module exposes it)."
  value       = try(module.gke.kubeconfig, try(module.eks.kubeconfig, try(module.aks.kubeconfig, null)))
  sensitive   = true
}

# Some modules expose a filesystem path to a kubeconfig they created locally.
output "kubeconfig_path" {
  description = "Path to a generated kubeconfig file (if the module emits one)."
  value       = try(module.gke.kubeconfig_path, try(module.eks.kubeconfig_path, try(module.aks.kubeconfig_path, null)))
}

# ------------ Node pools / groups --------------------------------------------

output "node_pools_input" {
  description = "The node_pools input provided to this stack (cloud-agnostic description)."
  value       = var.node_pools
}

output "node_pool_names" {
  description = "Effective node pool / node group names created by the module."
  value = coalescelist(
    try(module.gke.node_pool_names, []),
    try(module.eks.node_group_names, []),
    try(module.aks.node_pool_names, [])
  )
}

output "node_pool_ids" {
  description = "Cloud-specific identifiers for node pools / groups (if exposed)."
  value = coalescelist(
    try(module.gke.node_pool_ids, []),
    try(module.eks.node_group_arns, []),
    try(module.aks.node_pool_ids, [])
  )
}

# ------------ Add-ons toggles / DNS ------------------------------------------

output "addons" {
  description = "Add-ons toggles resolved for this deployment."
  value = {
    ingress_enabled       = var.ingress_enabled
    external_dns_enabled  = var.external_dns_enabled
    cert_manager_enabled  = var.cert_manager_enabled
    dns_zone_name         = var.dns_zone_name
  }
}

# ------------ Convenience hints ----------------------------------------------

output "kubectl_hint" {
  description = "Human-friendly hint for exporting a kubeconfig from outputs (if kubeconfig is provided)."
  value       = "If 'kubeconfig' is non-null, you can: terraform output -raw kubeconfig > ./kubeconfig && export KUBECONFIG=$PWD/kubeconfig"
}

output "context_info" {
  description = "Suggested kubectl context name (may differ depending on the provider tooling)."
  value       = format("%s-%s", var.cloud, var.cluster_name)
}
