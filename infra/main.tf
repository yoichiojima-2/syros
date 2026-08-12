terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# --- state: Firestore (control plane) + GCS (workspace/transcripts) ---

resource "google_firestore_database" "default" {
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

resource "google_storage_bucket" "sessions" {
  name                        = "${var.project}-syros"
  location                    = var.region
  uniform_bucket_level_access = true
  depends_on                  = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "syros" {
  repository_id = "syros"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# --- the sandbox identity: least privilege, no secrets ---

resource "google_service_account" "runner" {
  account_id   = "syros-runner"
  display_name = "syros sandbox runner"
}

resource "google_project_iam_member" "runner_vertex" {
  project = var.project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_firestore" {
  project = var.project
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_storage_bucket_iam_member" "runner_bucket" {
  bucket = google_storage_bucket.sessions.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runner.email}"
}

# --- the sandbox ---

resource "google_cloud_run_v2_job" "runner" {
  name                = var.job_name
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.runner.email
      timeout         = var.job_timeout
      max_retries     = 0

      containers {
        image = var.image
        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }
        env {
          name  = "SYROS_PROJECT"
          value = var.project
        }
        env {
          name  = "SYROS_BUCKET"
          value = google_storage_bucket.sessions.name
        }
        env {
          name  = "CLOUD_ML_REGION"
          value = var.vertex_region
        }
      }

      dynamic "vpc_access" {
        for_each = var.vpc_connector == null ? [] : [var.vpc_connector]
        content {
          connector = vpc_access.value
          egress    = "ALL_TRAFFIC"
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}
