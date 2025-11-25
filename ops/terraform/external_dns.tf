/*
  external_dns.tf — Create DNS records for ingress hosts (multi-cloud friendly)

  This module-less Terraform file lets you declare a list of DNS records
  (A/CNAME) for your ingress endpoints and materialize them on AWS Route53,
  Google Cloud DNS, or Azure DNS, controlled by var.dns_provider.

  Usage example (in a root *.tfvars or variables block):
    dns_provider = "route53" # or "gcloud" | "azure"

    gcp_project  = "my-gcp-project"   # only if dns_provider = "gcloud"
    azure_default_resource_group = "dns-rg"  # only if dns_provider = "azure"

    records = [
      {
        host      = "studio"
        zone_name = "animica.dev"
        type      = "CNAME"
        ttl       = 300
        targets   = ["lb-external-xyz.elb.amazonaws.com."] # hostname w/ trailing dot preferred
      },
      {
        host      = "explorer"
        zone_name = "animica.dev"
        type      = "A"
        ttl       = 300
        targets   = ["203.0.113.42"]  # static IP (GCLB, NLB, etc.)
      }
    ]

  Notes:
    - For apex records, set host = "@".
    - Route53 will accept list-of-1 for CNAME. GCP/Azure use rrdatas/records lists.
    - For Azure per-record resource groups, set records[i].azure_resource_group, else the default is used.
    - For GCP, if your managed zone *name* differs from the DNS zone (rare), set records[i].google_managed_zone.
*/

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0.0"
    }
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.100.0"
    }
  }
}

# ------------------------------- Inputs --------------------------------------

variable "dns_provider" {
  description = "Which provider to use for DNS records: route53 | gcloud | azure"
  type        = string
  validation {
    condition     = contains(["route53", "gcloud", "azure"], var.dns_provider)
    error_message = "dns_provider must be one of: route53, gcloud, azure."
  }
}

variable "records" {
  description = "List of ingress DNS records to create."
  type = list(object({
    host                  = string                 # subdomain or "@" for apex
    zone_name             = string                 # e.g. animica.dev (no trailing dot)
    type                  = string                 # A or CNAME
    ttl                   = number                 # seconds
    targets               = list(string)           # IP(s) for A, hostname(s) for CNAME
    azure_resource_group  = optional(string)       # per-record override
    google_managed_zone   = optional(string)       # if managed-zone name != zone_name
  }))
  validation {
    condition = alltrue([
      for r in var.records :
      (upper(r.type) == "A" || upper(r.type) == "CNAME")
      && length(r.targets) > 0
    ])
    error_message = "Each record must be type A or CNAME and include at least one target."
  }
}

# Only used when dns_provider = "gcloud"
variable "gcp_project" {
  description = "GCP project id for Cloud DNS."
  type        = string
  default     = null
}

# Only used when dns_provider = "azure"
variable "azure_default_resource_group" {
  description = "Default Azure resource group for DNS zones (overridden per record if provided)."
  type        = string
  default     = null
}

# ------------------------------ Locals ---------------------------------------

locals {
  use_aws   = var.dns_provider == "route53"
  use_gcp   = var.dns_provider == "gcloud"
  use_azure = var.dns_provider == "azure"

  # Convenience map: index → record
  recs = { for idx, r in var.records : idx => r }

  # Unique zone keys per provider
  zones_aws  = local.use_aws   ? toset([for r in var.records : r.zone_name]) : []
  zones_gcp  = local.use_gcp   ? toset([for r in var.records : coalesce(try(r.google_managed_zone, null), r.zone_name)]) : []
  zones_azrg = local.use_azure ? toset([for r in var.records : "${coalesce(try(r.azure_resource_group, null), var.azure_default_resource_group)}|${r.zone_name}"]) : []

  # Helpers
  fqdn = {
    for idx, r in local.recs :
    idx => (r.host == "@" ? "${r.zone_name}" : "${r.host}.${r.zone_name}")
  }
}

# ------------------------------- AWS Route53 ---------------------------------

data "aws_route53_zone" "zones" {
  for_each     = local.use_aws ? { for z in local.zones_aws : z => z } : {}
  name         = each.key
  private_zone = false
}

resource "aws_route53_record" "records" {
  for_each = local.use_aws ? local.recs : {}

  zone_id = data.aws_route53_zone.zones[each.value.zone_name].zone_id
  name    = local.fqdn[each.key]
  type    = upper(each.value.type)
  ttl     = each.value.ttl

  # For A: 'records'; For CNAME: still 'records' (single element).
  records = each.value.targets
}

# -------------------------------- GCP DNS ------------------------------------

data "google_dns_managed_zone" "zones" {
  for_each = local.use_gcp ? { for z in local.zones_gcp : z => z } : {}
  name     = each.key
  project  = var.gcp_project
}

resource "google_dns_record_set" "records" {
  for_each = local.use_gcp ? local.recs : {}

  project      = var.gcp_project
  managed_zone = data.google_dns_managed_zone.zones[coalesce(try(each.value.google_managed_zone, null), each.value.zone_name)].name
  name         = "${local.fqdn[each.key]}."
  type         = upper(each.value.type)
  ttl          = each.value.ttl
  rrdatas      = each.value.targets
}

# -------------------------------- Azure DNS ----------------------------------

provider "azurerm" {
  features {}
}

# Lookup zones by "resourceGroup|zoneName"
data "azurerm_dns_zone" "zones" {
  for_each            = local.use_azure ? { for key in local.zones_azrg : key => key } : {}
  name                = split("|", each.key)[1]
  resource_group_name = split("|", each.key)[0]
}

# A records
resource "azurerm_dns_a_record" "a" {
  for_each = local.use_azure ? {
    for idx, r in local.recs : idx => r if upper(r.type) == "A"
  } : {}

  name                = each.value.host == "@" ? "@" : each.value.host
  zone_name           = each.value.zone_name
  resource_group_name = coalesce(try(each.value.azure_resource_group, null), var.azure_default_resource_group)
  ttl                 = each.value.ttl
  records             = each.value.targets

  depends_on = [data.azurerm_dns_zone.zones]
}

# CNAME records
resource "azurerm_dns_cname_record" "cname" {
  for_each = local.use_azure ? {
    for idx, r in local.recs : idx => r if upper(r.type) == "CNAME"
  } : {}

  name                = each.value.host == "@" ? "@" : each.value.host
  zone_name           = each.value.zone_name
  resource_group_name = coalesce(try(each.value.azure_resource_group, null), var.azure_default_resource_group)
  ttl                 = each.value.ttl
  record              = element(each.value.targets, 0) # Azure CNAME expects a single target

  depends_on = [data.azurerm_dns_zone.zones]
}

# ------------------------------- Outputs -------------------------------------

output "dns_fqdns" {
  description = "FQDNs created (index → name)"
  value       = { for idx, r in local.recs : idx => local.fqdn[idx] }
}

output "aws_route53_record_names" {
  value       = local.use_aws ? [for r in aws_route53_record.records : r.name] : []
  description = "Route53 record names (if provider=route53)."
}

output "gcp_record_names" {
  value       = local.use_gcp ? [for r in google_dns_record_set.records : r.name] : []
  description = "GCP record-set names (if provider=gcloud)."
}

output "azure_record_names" {
  value       = local.use_azure ? concat(
    [for r in azurerm_dns_a_record.a    : r.fqdn],
    [for r in azurerm_dns_cname_record.cname : r.fqdn]
  ) : []
  description = "Azure DNS FQDNs (if provider=azure)."
}
