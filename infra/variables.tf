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

variable "vpc_connector" {
  description = "Optional Serverless VPC connector for egress lockdown; null = default egress"
  type        = string
  default     = null
}
