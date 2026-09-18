# WATTS supporting infrastructure.
#
# Scope: the control plane's own dependencies - stream, time-series store, cache, secrets,
# object storage for the audit anchor. Deliberately NOT in scope: the GPU fleet and the
# serving stack. WATTS observes those; it does not own them.

terraform {
  required_version = ">= 1.6"
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
    helm       = { source = "hashicorp/helm", version = "~> 2.13" }
    random     = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

variable "namespace" {
  description = "Kubernetes namespace for the WATTS control plane"
  type        = string
  default     = "watts"
}

variable "retention_days" {
  description = "Retention for raw request telemetry. Aggregates are kept for 13 months."
  type        = number
  default     = 30
}

variable "workloads" {
  description = "Workloads permitted to send telemetry. Each gets its own HMAC key."
  type        = list(string)
  default     = []
}

# One key per workload: a compromised key forges telemetry for that workload only.
resource "random_password" "workload_hmac" {
  for_each = toset(var.workloads)
  length   = 48
  special  = false
}

resource "kubernetes_secret" "workload_keys" {
  metadata {
    name      = "watts-workload-keys"
    namespace = var.namespace
  }
  data = {
    "keys.json" = jsonencode({
      for w in var.workloads : w => random_password.workload_hmac[w].result
    })
  }
  type = "Opaque"
}

module "stream" {
  source            = "./modules/stream"
  namespace         = var.namespace
  partitions        = 12
  retention_hours   = 72
}

module "timeseries" {
  source          = "./modules/timeseries"
  namespace       = var.namespace
  retention_days  = var.retention_days
  schema_sql_path = "${path.module}/../infrastructure/sql/001_schema.sql"
}

module "policy" {
  source        = "./modules/policy"
  namespace     = var.namespace
  policy_bundle = "${path.module}/../policies"
}

output "workload_key_secret" {
  value       = kubernetes_secret.workload_keys.metadata[0].name
  description = "Secret holding per-workload HMAC keys. Rotate on a schedule."
  sensitive   = true
}
