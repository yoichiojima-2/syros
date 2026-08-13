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
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# --- state: Firestore (control plane) + GCS (per-session state under
# sessions/, shared workspaces under workspaces/) ---

resource "google_firestore_database" "default" {
  name                              = "(default)"
  location_id                       = var.firestore_location
  type                              = "FIRESTORE_NATIVE"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  depends_on                        = [google_project_service.apis]
}

# Firestore is the only copy of the control plane (GCS holds per-session blobs,
# BigQuery is a disposable snapshot), so keep a rolling weekly backup
resource "google_firestore_backup_schedule" "weekly" {
  database  = google_firestore_database.default.name
  retention = "1209600s" # 14 days

  weekly_recurrence {
    day = "SUNDAY"
  }
}

# a deployment's run history filters sessions by deployment and orders by
# created_at; equality + order-by on different fields needs a composite index
resource "google_firestore_index" "sessions_by_deployment" {
  database   = google_firestore_database.default.name
  collection = "sessions"

  fields {
    field_path = "deployment"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

# the console's global approvals queue queries approvals across all sessions;
# collection-group queries need an explicit collection-group-scoped index
resource "google_firestore_field" "approvals_status" {
  database   = google_firestore_database.default.name
  collection = "approvals"
  field      = "status"

  index_config {
    # custom index_config replaces the field's default single-field indexes,
    # so keep the collection-scoped index that per-session queries rely on
    indexes {
      order       = "ASCENDING"
      query_scope = "COLLECTION"
    }
    indexes {
      order       = "ASCENDING"
      query_scope = "COLLECTION_GROUP"
    }
  }
}

resource "google_storage_bucket" "sessions" {
  name                        = "${var.project}-syros"
  location                    = var.region
  uniform_bucket_level_access = true
  depends_on                  = [google_project_service.apis]
}

# --- analysis: BigQuery dataset for `syros export` snapshots ---
# Loaded by the caller's identity (no runner/console access): callers need
# roles/bigquery.jobUser on the project and dataEditor on this dataset.

resource "google_bigquery_dataset" "analytics" {
  dataset_id  = var.dataset_id
  location    = var.region
  description = "Flat snapshots of the Firestore control plane, written by `syros export`"
  # Tables are disposable re-runnable snapshots; don't let them block destroy.
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
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

# Platform connectors: one secret per catalog entry, written by
# `syros connectors auth|set`, read by the runner at run start to mount the
# vendor's official hosted MCP server. Containers only, like the key above.
locals {
  connectors = toset(["slack", "notion", "github", "google"])
}

resource "google_secret_manager_secret" "connectors" {
  for_each  = local.connectors
  secret_id = "syros-connector-${each.key}"
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

# Connector credentials are the runner's to read — that is the whole feature.
# Scoped per secret, never project-wide; a container stays empty (and the
# grant moot) until an operator stores a credential for that connector.
resource "google_secret_manager_secret_iam_member" "runner_connectors" {
  for_each  = google_secret_manager_secret.connectors
  secret_id = each.value.id
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

# The runner is always triggered with a container override (the session id is
# passed as the job argument), which needs run.jobs.runWithOverrides — a
# permission run.invoker does not carry.
resource "google_cloud_run_v2_job_iam_member" "console_runs_job" {
  name     = google_cloud_run_v2_job.runner.name
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.console.email}"
}

# The workspaces and artifacts pages list and preview objects in the session
# bucket, and workspace files are editable from the console (edit in place,
# upload, delete). objectUser rather than the runner's objectAdmin: the console
# needs to read, write and delete objects, never to set IAM policy on them.
resource "google_storage_bucket_iam_member" "console_bucket" {
  bucket = google_storage_bucket.sessions.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.console.email}"
}

# The connectors page shows whether each connector has a stored credential.
# viewer reads secret + version *metadata* (state, create time) only — the
# console can never access a payload; credentials are written from an
# operator's machine via `syros connectors auth|set`.
resource "google_secret_manager_secret_iam_member" "console_connectors_viewer" {
  for_each  = google_secret_manager_secret.connectors
  secret_id = each.value.id
  role      = "roles/secretmanager.viewer"
  member    = "serviceAccount:${google_service_account.console.email}"
}

# Who may open the console. No allUsers here by design: reach it with
# `gcloud run services proxy`, which authenticates as the caller.
resource "google_cloud_run_v2_service_iam_member" "console_invokers" {
  for_each = toset(var.console_invokers)
  name     = google_cloud_run_v2_service.console.name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

# --- the scheduler: Cloud Scheduler fires `syros tick` on a fixed cadence ---
# The tick reads deployments/ from Firestore, fires whatever is due (create
# session, queue prompt, trigger the runner job) and exits. It is idempotent —
# a slot is consumed by a Firestore transaction — so overlap and retry are safe.

resource "google_service_account" "scheduler" {
  account_id   = "syros-scheduler"
  display_name = "syros deployment tick"
}

resource "google_project_iam_member" "scheduler_firestore" {
  project = var.project
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

# The tick triggers the runner job for each firing, exactly as a client would —
# with a container override carrying the session id, hence
# jobsExecutorWithOverrides rather than plain run.invoker.
resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_job" {
  name     = google_cloud_run_v2_job.runner.name
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_job" "scheduler" {
  name                = var.scheduler_job_name
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.scheduler.email
      timeout         = "300s"
      max_retries     = 0

      containers {
        image   = var.image
        command = ["syros"]
        args    = ["tick"]
        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
        env {
          name  = "SYROS_PROJECT"
          value = var.project
        }
        env {
          name  = "SYROS_REGION"
          value = var.region
        }
        env {
          name  = "SYROS_JOB"
          value = var.job_name
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Cloud Scheduler can't run a Cloud Run Job directly; it POSTs the job's :run
# endpoint with an OAuth token. The scheduler SA invokes its own tick job, so
# one identity covers the whole chain: scheduler -> tick -> runner.
resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_tick" {
  name     = google_cloud_run_v2_job.scheduler.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "tick" {
  name        = "syros-tick"
  region      = var.region
  schedule    = var.tick_schedule
  description = "Fire due syros deployments (the tick cadence bounds deployment granularity)"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project}/locations/${var.region}/jobs/${google_cloud_run_v2_job.scheduler.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
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
      # Prompting an idle session re-triggers the runner job, which is looked up
      # by (project, region, job) — the default region is only right by accident.
      env {
        name  = "SYROS_REGION"
        value = var.region
      }
      env {
        name  = "SYROS_BUCKET"
        value = google_storage_bucket.sessions.name
      }
    }
  }

  depends_on = [google_project_service.apis]
}
