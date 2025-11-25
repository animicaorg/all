/*
  providers, kubeconfig wiring, and base namespaces

  This file wires the Kubernetes & Helm providers to whatever cluster module
  you selected in main.tf (GKE/EKS/AKS), preferring a kubeconfig *path* when
  available. If only a kubeconfig *content* blob is available, we write it to
  a local file and point providers at that path.

  You can also override with:
    - var.kubeconfig_path_override (absolute or relative path)
    - var.kubeconfig_content_override (full kubeconfig text)
    - var.kubeconfig_write_path (where to write the content; defaults under this module)

  Finally, we create the base namespace used by the rest of the k8s manifests:
    - var.cluster_namespace (defaults to "animica-devnet")
*/

terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.31.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.12.0"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.4.0"
    }
  }
}

# --------------------------- Inputs (overrides) -------------------------------

# Optional: If you already have a kubeconfig file on disk, point to it here.
variable "kubeconfig_path_override" {
  description = "Path to an existing kubeconfig file to use (overrides module outputs)."
  type        = string
  default     = null
}

# Optional: If you have the kubeconfig *content* but not a file, put it here.
variable "kubeconfig_content_override" {
  description = "Raw kubeconfig content (YAML). If set, it will be written to kubeconfig_write_path."
  type        = string
  default     = null
  sensitive   = true
}

# Optional: Where to write the kubeconfig content if needed.
variable "kubeconfig_write_path" {
  description = "Where to write a generated kubeconfig file if we only have inline content."
  type        = string
  default     = null
}

# Namespace used by all Animica workloads.
variable "cluster_namespace" {
  description = "Kubernetes namespace for Animica devnet workloads."
  type        = string
  default     = "animica-devnet"
}

# -------------------------- Kubeconfig Resolution -----------------------------

locals {
  # Try to discover a kubeconfig path from whichever cloud module is active.
  module_kubeconfig_path = try(module.gke.kubeconfig_path,
                          try(module.eks.kubeconfig_path,
                          try(module.aks.kubeconfig_path, null)))

  # Try to discover kubeconfig *content* from modules (some expose the blob).
  module_kubeconfig_content = try(module.gke.kubeconfig,
                             try(module.eks.kubeconfig,
                             try(module.aks.kubeconfig, null)))

  # Choose the final path to use for providers (override > module path > write-path or default).
  kubeconfig_path_final = coalesce(
    var.kubeconfig_path_override,
    local.module_kubeconfig_path,
    coalesce(var.kubeconfig_write_path, "${path.module}/.generated/kubeconfig")
  )

  # Do we need to emit a file because we only have content (no path overrides)?
  need_to_write_kubeconfig = (
    var.kubeconfig_path_override == null
    && local.module_kubeconfig_path == null
    && length(coalesce(var.kubeconfig_content_override, local.module_kubeconfig_content, "")) > 0
  )
}

# Ensure the target directory exists for a generated kubeconfig.
resource "local_file" "kubeconfig_dir_placeholder" {
  count    = local.need_to_write_kubeconfig ? 1 : 0
  filename = "${dirname(local.kubeconfig_path_final)}/.keep"
  content  = ""
}

# Write kubeconfig content to disk if needed (sensitive).
resource "local_sensitive_file" "kubeconfig" {
  count           = local.need_to_write_kubeconfig ? 1 : 0
  filename        = local.kubeconfig_path_final
  content         = coalesce(var.kubeconfig_content_override, local.module_kubeconfig_content)
  file_permission = "0600"

  depends_on = [local_file.kubeconfig_dir_placeholder]
}

# ------------------------------ Providers ------------------------------------

# Primary Kubernetes provider used by subsequent resources/charts.
provider "kubernetes" {
  config_path = local.kubeconfig_path_final
}

# Helm provider (points at the same cluster).
provider "helm" {
  kubernetes {
    config_path = local.kubeconfig_path_final
  }
}

# ------------------------------ Namespaces -----------------------------------

resource "kubernetes_namespace_v1" "animica" {
  metadata {
    name = var.cluster_namespace
    labels = {
      "app.kubernetes.io/name"       = "animica"
      "app.kubernetes.io/managed-by" = "terraform"
      "animica.dev/role"             = "devnet"
    }
  }
}

# Optional convenience: create common observability labels in the same namespace.
# (Most of our kustomize/helm overlays expect a single namespace layout.)
resource "kubernetes_resource_quota_v1" "soft_limits" {
  metadata {
    name      = "soft-limits"
    namespace = kubernetes_namespace_v1.animica.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of" = "animica-observability"
    }
  }

  spec {
    hard = {
      "requests.cpu"    = "200"
      "requests.memory" = "256Gi"
      "limits.cpu"      = "400"
      "limits.memory"   = "512Gi"
      "pods"            = "2000"
      "services"        = "500"
      "configmaps"      = "2000"
      "secrets"         = "4000"
    }
    scope_selector {
      match_expressions {
        operator = "In"
        scope    = "PriorityClass"
        values   = ["", ""] # no-op placeholder; edit if you enforce priority classes
      }
    }
  }
}

# A few sane defaults for image pull backoffs to reduce noisy restarts during devnet churn.
resource "kubernetes_limit_range_v1" "defaults" {
  metadata {
    name      = "defaults"
    namespace = kubernetes_namespace_v1.animica.metadata[0].name
  }

  spec {
    limit {
      type = "Container"
      default = {
        cpu    = "250m"
        memory = "256Mi"
      }
      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }
    }
  }
}
