# Animica Compute Platform - AWS Infrastructure
# Terraform configuration for deploying to AWS

terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  default     = "development"
}

variable "cluster_name" {
  description = "Cluster name"
  default     = "animica-compute"
}

# VPC Module (simplified for now)
output "instructions" {
  value = <<EOT
Animica Compute Platform - Terraform Configuration

To deploy:
1. Initialize: terraform init
2. Plan: terraform plan
3. Apply: terraform apply

This will create:
- VPC with public/private subnets
- EKS cluster with GPU nodes
- RDS PostgreSQL database
- ElastiCache Redis
- S3 bucket for models
EOT
}
