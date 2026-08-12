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
    "secretmanager.googleapis.com",
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

# --- secrets: containers only; values are added out-of-band via gcloud ---

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "anthropic-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# --- the sandbox identity: least privilege; a secret only on the escape hatch ---

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

# Granted only when the sandbox actually calls Anthropic directly, so the default
# deployment keeps a runner identity that can read no secret at all.
resource "google_secret_manager_secret_iam_member" "runner_anthropic_key" {
  count     = var.model_backend == "anthropic" ? 1 : 0
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runner.email}"
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
        env {
          name  = "SYROS_MODEL_BACKEND"
          value = var.model_backend
        }

        dynamic "env" {
          for_each = var.model_backend == "anthropic" ? [1] : []
          content {
            name = "ANTHROPIC_API_KEY"
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.anthropic_api_key.secret_id
                version = "latest"
              }
            }
          }
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

  # The secretAccessor binding must exist before Cloud Run validates the mount,
  # or the job update fails with a permission error on the secret.
  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.runner_anthropic_key,
  ]
}

# --- the console: same image, IAM-protected Cloud Run service ---
# No public access; connect with `gcloud run services proxy syros-console`.

resource "google_service_account" "console" {
  account_id   = "syros-console"
  display_name = "syros console"
}

resource "google_project_iam_member" "console_firestore" {
  project = var.project
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.console.email}"
}

resource "google_cloud_run_v2_job_iam_member" "console_runs_job" {
  name     = google_cloud_run_v2_job.runner.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.console.email}"
}

resource "google_cloud_run_v2_service" "console" {
  name                = var.console_name
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.console.email

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image   = var.image
      command = ["syros"]
      args    = ["console", "--host", "0.0.0.0", "--no-open"]

      env {
        name  = "SYROS_PROJECT"
        value = var.project
      }
      env {
        name  = "SYROS_JOB"
        value = var.job_name
      }
    }
  }

  depends_on = [google_project_service.apis]
}
