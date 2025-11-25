/*
  monitoring.tf — Dedicated node groups/pools for observability (Prometheus/Grafana/Loki/Tempo).

  This module lets you provision a tainted, labeled node pool so your observability
  stack can be scheduled away from latency-sensitive workloads. It supports one
  (or more) of GKE, EKS, and AKS via per-provider toggles.

  Common traits applied to all pools:
    - Labels:
        workload=observability
        animica.dev/purpose=observability
    - Taint:
        dedicated=observability:NoSchedule
    - Reasonable autoscaling defaults

  Usage (pick your platform, provide cluster inputs in terraform.tfvars):
    # --- GKE example ---
    enable_gke_obs_pool = true
    gke_project_id      = "my-gcp-project"
    gke_location        = "us-central1"
    gke_cluster_name    = "animica-devnet"

    # --- EKS example ---
    enable_eks_obs_group = true
    eks_cluster_name     = "animica-devnet"
    eks_subnet_ids       = ["subnet-aaa", "subnet-bbb"]
    # Option A) pass your pre-created node role:
    # eks_node_role_arn  = "arn:aws:iam::123456789012:role/EKSNodeRole"
    # Option B) let Terraform create a minimal role:
    eks_create_node_role = true

    # --- AKS example ---
    enable_aks_obs_pool = true
    aks_cluster_id      = "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ContainerService/managedClusters/<name>"

  Notes:
    - Providers (google, aws, azurerm) should already be configured in main.tf.
    - For EKS, you must provide subnet IDs belonging to the cluster VPC.
    - For AKS, we reference the existing cluster by ID.
*/

# -------------------------------- Locals -------------------------------------

locals {
  obs_labels = {
    workload                   = "observability"
    "animica.dev/purpose"      = "observability"
    "app.kubernetes.io/name"   = "observability"
    "app.kubernetes.io/part-of"= "animica"
  }
  obs_taint_key   = "dedicated"
  obs_taint_value = "observability"
  # Effects vary by provider spelling; we use NO_SCHEDULE where required.
}

# --------------------------------- Inputs ------------------------------------

# Global toggles
variable "enable_gke_obs_pool" { type = bool, default = false }
variable "enable_eks_obs_group" { type = bool, default = false }
variable "enable_aks_obs_pool" { type = bool, default = false }

# ------- GKE -------
variable "gke_project_id"   { type = string, default = null }
variable "gke_location"     { type = string, default = null } # zone or region
variable "gke_cluster_name" { type = string, default = null }
variable "gke_obs_pool_name" { type = string, default = "obs-pool" }
variable "gke_machine_type" { type = string, default = "e2-standard-4" }
variable "gke_disk_size_gb" { type = number, default = 50 }
variable "gke_min_nodes"    { type = number, default = 1 }
variable "gke_max_nodes"    { type = number, default = 3 }
variable "gke_desired_nodes"{ type = number, default = 1 }
variable "gke_spot"         { type = bool,   default = false } # use preemptible/spot nodes

# ------- EKS -------
variable "eks_cluster_name" { type = string, default = null }
variable "eks_subnet_ids"   { type = list(string), default = [] }
variable "eks_obs_group_name" { type = string, default = "obs-group" }
variable "eks_instance_types" { type = list(string), default = ["t3.large"] }
variable "eks_min_size"     { type = number, default = 1 }
variable "eks_max_size"     { type = number, default = 3 }
variable "eks_desired_size" { type = number, default = 1 }
variable "eks_disk_size"    { type = number, default = 40 }
variable "eks_create_node_role" { type = bool, default = false }
variable "eks_node_role_arn" { type = string, default = null }
variable "eks_capacity_type" { type = string, default = "ON_DEMAND" } # or "SPOT"

# ------- AKS -------
variable "aks_cluster_id"        { type = string, default = null }
variable "aks_obs_pool_name"     { type = string, default = "obsnp" } # 3-12 chars
variable "aks_vm_size"           { type = string, default = "Standard_D4s_v5" }
variable "aks_min_count"         { type = number, default = 1 }
variable "aks_max_count"         { type = number, default = 3 }
variable "aks_node_count"        { type = number, default = 1 } # used at creation
variable "aks_enable_auto_scale" { type = bool,   default = true }
variable "aks_mode"              { type = string, default = "User" }
variable "aks_os_disk_size_gb"   { type = number, default = 64 }
variable "aks_spot"              { type = bool,   default = false }

# ------------------------------ GKE Node Pool --------------------------------

resource "google_container_node_pool" "obs" {
  count              = var.enable_gke_obs_pool ? 1 : 0
  project            = var.gke_project_id
  location           = var.gke_location
  cluster            = var.gke_cluster_name
  name               = var.gke_obs_pool_name
  initial_node_count = var.gke_desired_nodes

  node_config {
    machine_type = var.gke_machine_type
    disk_size_gb = var.gke_disk_size_gb
    labels       = local.obs_labels
    tags         = ["observability", "animica"]

    # GKE spot/preemptible
    preemptible = var.gke_spot

    taint {
      key    = local.obs_taint_key
      value  = local.obs_taint_value
      effect = "NO_SCHEDULE"
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring",
      "https://www.googleapis.com/auth/devstorage.read_only",
    ]
  }

  autoscaling {
    min_node_count = var.gke_min_nodes
    max_node_count = var.gke_max_nodes
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
    strategy        = "SURGE"
  }

  lifecycle {
    ignore_changes = [
      node_config[0].oauth_scopes, # GKE often mutates scopes
    ]
  }
}

# ------------------------------ EKS Node Group -------------------------------

# Optionally create a minimal IAM role for the node group
data "aws_iam_policy" "eks_worker_node" {
  count = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  arn   = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

data "aws_iam_policy" "eks_cni" {
  count = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  arn   = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

data "aws_iam_policy" "ecr_readonly" {
  count = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  arn   = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role" "eks_node" {
  count = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  name  = "eks-node-observability"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action    = "sts:AssumeRole",
        Effect    = "Allow",
        Principal = { Service = "ec2.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eks_node_attach_worker" {
  count      = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  role       = aws_iam_role.eks_node[0].name
  policy_arn = data.aws_iam_policy.eks_worker_node[0].arn
}
resource "aws_iam_role_policy_attachment" "eks_node_attach_cni" {
  count      = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  role       = aws_iam_role.eks_node[0].name
  policy_arn = data.aws_iam_policy.eks_cni[0].arn
}
resource "aws_iam_role_policy_attachment" "eks_node_attach_ecr" {
  count      = var.enable_eks_obs_group && var.eks_create_node_role ? 1 : 0
  role       = aws_iam_role.eks_node[0].name
  policy_arn = data.aws_iam_policy.ecr_readonly[0].arn
}

locals {
  eks_node_role_arn_final = var.enable_eks_obs_group ? (var.eks_create_node_role ? aws_iam_role.eks_node[0].arn : var.eks_node_role_arn) : null
}

resource "aws_eks_node_group" "obs" {
  count         = var.enable_eks_obs_group ? 1 : 0
  cluster_name  = var.eks_cluster_name
  node_group_name = var.eks_obs_group_name
  node_role_arn = local.eks_node_role_arn_final
  subnet_ids    = var.eks_subnet_ids
  capacity_type = var.eks_capacity_type

  scaling_config {
    min_size     = var.eks_min_size
    max_size     = var.eks_max_size
    desired_size = var.eks_desired_size
  }

  disk_size      = var.eks_disk_size
  instance_types = var.eks_instance_types
  ami_type       = "AL2_x86_64"

  labels = local.obs_labels

  taint {
    key    = local.obs_taint_key
    value  = local.obs_taint_value
    effect = "NO_SCHEDULE"
  }

  update_config {
    max_unavailable = 1
  }

  tags = {
    "workload"                = "observability"
    "animica.dev/purpose"     = "observability"
    "kubernetes.io/cluster/${var.eks_cluster_name}" = "owned"
  }

  lifecycle {
    ignore_changes = [
      labels, taint, tags, # EKS may reorder/normalize
    ]
  }
}

# -------------------------------- AKS Node Pool ------------------------------

resource "azurerm_kubernetes_cluster_node_pool" "obs" {
  count                 = var.enable_aks_obs_pool ? 1 : 0
  kubernetes_cluster_id = var.aks_cluster_id
  name                  = var.aks_obs_pool_name
  mode                  = var.aks_mode
  vm_size               = var.aks_vm_size

  enable_auto_scaling = var.aks_enable_auto_scale
  min_count           = var.aks_enable_auto_scale ? var.aks_min_count : null
  max_count           = var.aks_enable_auto_scale ? var.aks_max_count : null
  node_count          = var.aks_enable_auto_scale ? null : var.aks_node_count

  os_disk_size_gb     = var.aks_os_disk_size_gb

  node_labels = local.obs_labels

  node_taints = [
    "${local.obs_taint_key}=${local.obs_taint_value}:NoSchedule",
  ]

  # Spot support if desired
  priority = var.aks_spot ? "Spot" : "Regular"

  orchestrator_version = null # inherit from cluster
  upgrade_settings {
    max_surge = "33%"
  }

  tags = {
    workload               = "observability"
    "animica.dev/purpose"  = "observability"
  }
}

# -------------------------------- Outputs ------------------------------------

output "observability_labels" {
  description = "Standard labels applied to observability node pools."
  value       = local.obs_labels
}

output "observability_taint" {
  description = "Standard taint applied to observability node pools."
  value = {
    key    = local.obs_taint_key
    value  = local.obs_taint_value
    effect = "NoSchedule"
  }
}

output "gke_obs_pool_name" {
  value       = try(google_container_node_pool.obs[0].name, null)
  description = "GKE node pool name (if created)."
}

output "eks_obs_group_name" {
  value       = try(aws_eks_node_group.obs[0].node_group_name, null)
  description = "EKS managed node group name (if created)."
}

output "aks_obs_pool_name" {
  value       = try(azurerm_kubernetes_cluster_node_pool.obs[0].name, null)
  description = "AKS node pool name (if created)."
}
