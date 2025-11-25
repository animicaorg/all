/*
  Shared variables for Animica multi-cloud Terraform.

  These variables are intentionally cloud-agnostic where possible.
  - `cloud` selects one of: gke | eks | aks
  - `node_class` in node_pools is a generic size string mapped by each module:
      * GKE -> machine_type
      * EKS -> instance_type
      * AKS -> vm_size
*/

variable "cloud" {
  description = "Target cloud for the Kubernetes control plane: one of gke, eks, aks."
  type        = string
  default     = "gke"

  validation {
    condition     = contains(["gke", "eks", "aks"], var.cloud)
    error_message = "cloud must be one of: gke, eks, aks."
  }
}

# -------- Cloud credentials / IDs (required conditionally) -------------------

variable "project_id" {
  description = "GCP project id (required when cloud = gke)."
  type        = string
  default     = null

  validation {
    condition     = var.cloud != "gke" || (var.project_id != null && length(trim(var.project_id)) > 0)
    error_message = "project_id is required when cloud == \"gke\"."
  }
}

variable "aws_account_id" {
  description = "AWS account id (required when cloud = eks)."
  type        = string
  default     = null

  validation {
    condition     = var.cloud != "eks" || (var.aws_account_id != null && length(trim(var.aws_account_id)) > 0)
    error_message = "aws_account_id is required when cloud == \"eks\"."
  }
}

variable "azure_subscription_id" {
  description = "Azure subscription id (required when cloud = aks)."
  type        = string
  default     = null

  validation {
    condition     = var.cloud != "aks" || (var.azure_subscription_id != null && length(trim(var.azure_subscription_id)) > 0)
    error_message = "azure_subscription_id is required when cloud == \"aks\"."
  }
}

# -------- Cluster identity & location ---------------------------------------

variable "region" {
  description = "Primary region for the cluster (e.g., us-central1, us-east-1, eastus)."
  type        = string

  validation {
    condition     = length(trim(var.region)) > 0
    error_message = "region must be a non-empty string."
  }
}

variable "zones" {
  description = "Optional list of zones/availability zones. Leave empty to let the module decide."
  type        = list(string)
  default     = []
}

variable "cluster_name" {
  description = "Kubernetes cluster name (DNS-label friendly)."
  type        = string
  default     = "animica-devnet"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.cluster_name)) && length(var.cluster_name) <= 40
    error_message = "cluster_name must match ^[a-z0-9-]+$ and be <= 40 chars."
  }
}

variable "k8s_version" {
  description = "Desired Kubernetes minor version (e.g., 1.29). Provider may pin patch."
  type        = string
  default     = "1.29"
}

# -------- Node pools ---------------------------------------------------------

variable "node_pools" {
  description = <<EOT
List of node pool definitions (cloud-agnostic). Each object:
  {
    name         = string               # pool name
    min_size     = number               # autoscaler min
    max_size     = number               # autoscaler max
    desired_size = number               # initial size (between min..max)
    node_class   = string               # generic size (GKE: machine_type, EKS: instance_type, AKS: vm_size)
    disk_gb      = number               # node OS disk size (>= 20 recommended)
    spot         = bool                 # use spot/preemptible if true
    labels       = map(string)          # node labels
    taints       = list(string)         # e.g., ["workload=miner:NoSchedule"]
  }

Example:
  node_pools = [
    {
      name         = "default"
      min_size     = 1
      max_size     = 2
      desired_size = 1
      node_class   = "standard-4"  # e.g., e2-standard-4 (GKE) / m6i.large (EKS) / Standard_D4s_v5 (AKS)
      disk_gb      = 100
      spot         = false
      labels       = { role = "general" }
      taints       = []
    }
  ]
EOT
  type = list(object({
    name         = string
    min_size     = number
    max_size     = number
    desired_size = number
    node_class   = string
    disk_gb      = number
    spot         = bool
    labels       = map(string)
    taints       = list(string)
  }))

  default = [
    {
      name         = "default"
      min_size     = 1
      max_size     = 2
      desired_size = 1
      node_class   = "standard-4"
      disk_gb      = 100
      spot         = false
      labels       = {}
      taints       = []
    }
  ]

  validation {
    condition = length(var.node_pools) > 0
    error_message = "At least one node pool must be defined."
  }

  validation {
    condition = alltrue([
      for p in var.node_pools :
      p.min_size <= p.desired_size && p.desired_size <= p.max_size
    ])
    error_message = "Each node pool must satisfy min_size <= desired_size <= max_size."
  }

  validation {
    condition = alltrue([
      for p in var.node_pools :
      p.disk_gb >= 20
    ])
    error_message = "Each node pool must have disk_gb >= 20."
  }

  validation {
    condition = alltrue([
      for p in var.node_pools :
      length(trim(p.node_class)) > 0 && length(trim(p.name)) > 0
    ])
    error_message = "Each node pool must have non-empty name and node_class."
  }
}

# -------- Tagging / labels ---------------------------------------------------

variable "tags" {
  description = "Common tags/labels applied to cloud resources (module-dependent)."
  type        = map(string)
  default     = {
    project = "animica"
    env     = "dev"
  }
}

# -------- Optional: Add-ons toggles & DNS -----------------------------------

variable "ingress_enabled" {
  description = "Enable an ingress controller via Helm (nginx or cloud-native, module-specific)."
  type        = bool
  default     = false
}

variable "external_dns_enabled" {
  description = "Enable external-dns to manage DNS records for ingress."
  type        = bool
  default     = false
}

variable "cert_manager_enabled" {
  description = "Enable cert-manager for ACME certificates."
  type        = bool
  default     = false
}

variable "dns_zone_name" {
  description = "Authoritative DNS zone name (e.g., animica.dev). Required if external_dns_enabled = true."
  type        = string
  default     = null

  validation {
    condition     = !var.external_dns_enabled || (var.dns_zone_name != null && length(trim(var.dns_zone_name)) > 0)
    error_message = "dns_zone_name must be set when external_dns_enabled is true."
  }
}
