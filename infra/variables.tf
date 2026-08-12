variable "project" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast1"
}

variable "firestore_location" {
  type    = string
  default = "asia-northeast1"
}

variable "image" {
  description = "Runner image, e.g. {region}-docker.pkg.dev/{project}/syros/runner:latest"
  type        = string
}

variable "job_name" {
  type    = string
  default = "syros-runner"
}

variable "job_timeout" {
  description = "Max duration of one sandbox execution"
  type        = string
  default     = "3600s"
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "2Gi"
}

variable "vertex_region" {
  description = "CLOUD_ML_REGION for Claude on Vertex (currently only 'global' serves Claude)"
  type        = string
  default     = "global"
}

variable "model_backend" {
  description = "Where model calls go: 'vertex' (inside the project) or 'anthropic' (mounts the anthropic-api-key secret; traffic leaves GCP)"
  type        = string
  default     = "vertex"

  validation {
    condition     = contains(["vertex", "anthropic"], var.model_backend)
    error_message = "model_backend must be 'vertex' or 'anthropic'."
  }
}

variable "dataset_id" {
  description = "BigQuery dataset that `syros export` loads analysis snapshots into"
  type        = string
  default     = "syros"
}

variable "console_name" {
  type    = string
  default = "syros-console"
}

variable "vpc_connector" {
  description = "Optional Serverless VPC connector for egress lockdown; null = default egress"
  type        = string
  default     = null
}
