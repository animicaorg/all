/*
  letsencrypt.tf — Bootstrap cert-manager via Helm and create Let's Encrypt ClusterIssuers.

  What this does:
    - Installs cert-manager (with CRDs) into a namespace (default: cert-manager)
    - Creates ClusterIssuer objects for both staging and production ACME endpoints
      using HTTP-01 solver against a chosen ingress class (default: nginx)

  Prereqs:
    - Kubernetes + Helm providers configured in your root (kubeconfig/context set)
    - An ingress controller installed and handling the chosen ingress class

  Quick start (terraform.tfvars):
    acme_email = "you@example.com"
    ingress_class = "nginx"
    # Optional:
    # issuer_staging_enabled = true
    # issuer_prod_enabled    = true

  Apply:
    terraform init
    terraform apply
*/

terraform {
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.12.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.25.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.9.0"
    }
  }
}

# ------------------------------- Inputs --------------------------------------

variable "certmanager_namespace" {
  description = "Namespace for cert-manager install."
  type        = string
  default     = "cert-manager"
}

variable "create_namespace" {
  description = "Whether to create the cert-manager namespace."
  type        = bool
  default     = true
}

variable "cert_manager_chart_version" {
  description = "Jetstack cert-manager Helm chart version."
  type        = string
  # Pin a sane default; bump as needed.
  default     = "v1.14.5"
}

variable "acme_email" {
  description = "Contact email for Let’s Encrypt (required by ACME)."
  type        = string
  validation {
    condition     = can(regex("@", var.acme_email))
    error_message = "acme_email must contain '@'."
  }
}

variable "ingress_class" {
  description = "Ingress class used by the HTTP-01 solver (e.g., nginx, traefik)."
  type        = string
  default     = "nginx"
}

variable "issuer_staging_enabled" {
  description = "Create the staging ClusterIssuer (useful for dry runs)."
  type        = bool
  default     = true
}

variable "issuer_prod_enabled" {
  description = "Create the production ClusterIssuer."
  type        = bool
  default     = true
}

variable "clusterissuer_staging_name" {
  description = "Name of the staging ClusterIssuer."
  type        = string
  default     = "letsencrypt-staging"
}

variable "clusterissuer_prod_name" {
  description = "Name of the production ClusterIssuer."
  type        = string
  default     = "letsencrypt-prod"
}

variable "acme_server_staging" {
  description = "Staging ACME directory URL."
  type        = string
  default     = "https://acme-staging-v02.api.letsencrypt.org/directory"
}

variable "acme_server_prod" {
  description = "Production ACME directory URL."
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
}

# ------------------------------- Namespace -----------------------------------

resource "kubernetes_namespace" "cert_manager" {
  count = var.create_namespace ? 1 : 0
  metadata {
    name = var.certmanager_namespace
    labels = {
      "app.kubernetes.io/name"       = "cert-manager"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# --------------------------------- Helm --------------------------------------

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  namespace        = var.certmanager_namespace
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = var.cert_manager_chart_version
  create_namespace = var.create_namespace
  wait             = true
  timeout          = 600

  # Install CRDs as part of the Helm release
  set {
    name  = "installCRDs"
    value = true
  }

  # Enable Prometheus metrics by default (safe; dashboards can scrape)
  set {
    name  = "prometheus.enabled"
    value = true
  }

  depends_on = [kubernetes_namespace.cert_manager]
}

# Give the API server a small window to register CRDs before we post ClusterIssuers.
resource "time_sleep" "after_cert_manager" {
  depends_on      = [helm_release.cert_manager]
  create_duration = "20s"
}

# ----------------------------- ClusterIssuers --------------------------------

# Staging ClusterIssuer (HTTP-01)
resource "kubernetes_manifest" "letsencrypt_staging" {
  count    = var.issuer_staging_enabled ? 1 : 0
  manifest = {
    apiVersion = "cert-manager.io/v1"
    kind       = "ClusterIssuer"
    metadata = {
      name = var.clusterissuer_staging_name
    }
    spec = {
      acme = {
        email  = var.acme_email
        server = var.acme_server_staging
        privateKeySecretRef = {
          name = var.clusterissuer_staging_name
        }
        solvers = [
          {
            http01 = {
              ingress = {
                # cert-manager >= v1.1 supports 'class' field
                class = var.ingress_class
              }
            }
          }
        ]
      }
    }
  }
  depends_on = [time_sleep.after_cert_manager]
}

# Production ClusterIssuer (HTTP-01)
resource "kubernetes_manifest" "letsencrypt_prod" {
  count    = var.issuer_prod_enabled ? 1 : 0
  manifest = {
    apiVersion = "cert-manager.io/v1"
    kind       = "ClusterIssuer"
    metadata = {
      name = var.clusterissuer_prod_name
    }
    spec = {
      acme = {
        email  = var.acme_email
        server = var.acme_server_prod
        privateKeySecretRef = {
          name = var.clusterissuer_prod_name
        }
        solvers = [
          {
            http01 = {
              ingress = {
                class = var.ingress_class
              }
            }
          }
        ]
      }
    }
  }
  depends_on = [time_sleep.after_cert_manager]
}

# ------------------------------- Outputs -------------------------------------

output "cert_manager_namespace" {
  value       = var.certmanager_namespace
  description = "Namespace where cert-manager is installed."
}

output "cluster_issuers" {
  description = "ClusterIssuers created (names)."
  value = compact([
    var.issuer_staging_enabled ? var.clusterissuer_staging_name : "",
    var.issuer_prod_enabled    ? var.clusterissuer_prod_name    : "",
  ])
}

