terraform {
  # State lives in GCS, not on whoever ran apply last. The bucket is versioned
  # and deliberately unmanaged by this config — it has to outlive any single
  # apply, and a state bucket that stores its own state is a bootstrap knot.
  backend "gcs" {
    bucket = "syros-505320-tfstate"
    prefix = "terraform/state"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.47"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.47"
    }
  }
}

provider "google-beta" {
  project = var.project
  region  = var.region
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
    "iap.googleapis.com",
    # for egress_control (enabled unconditionally: toggling the for_each set
    # on a flag churns every service resource, and disable_on_destroy=false
    # means an unused enabled API costs nothing)
    "compute.googleapis.com",
    "dns.googleapis.com",
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

# journal tailing filters one branch and orders by seq (store.list_events);
# equality + order-by on different fields needs a composite index
resource "google_firestore_index" "events_by_branch" {
  database   = google_firestore_database.default.name
  collection = "events"

  fields {
    field_path = "branch"
    order      = "ASCENDING"
  }
  fields {
    field_path = "seq"
    order      = "ASCENDING"
  }
}

# recover_head reads one branch's newest record (branch == + seq DESC);
# composite indexes are direction-sensitive, so the ASC pair above can't serve it
resource "google_firestore_index" "events_by_branch_desc" {
  database   = google_firestore_database.default.name
  collection = "events"

  fields {
    field_path = "branch"
    order      = "ASCENDING"
  }
  fields {
    field_path = "seq"
    order      = "DESCENDING"
  }
}

# the audit trail reads tool_call records out of the journal in write order
# (store.list_tool_calls): type equality + ts order-by
resource "google_firestore_index" "events_by_type" {
  database   = google_firestore_database.default.name
  collection = "events"

  fields {
    field_path = "type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "ts"
    order      = "ASCENDING"
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

# The audit trail: append-only per-run rows streamed by the runner at session
# release. Unlike the snapshot tables (which `syros export` truncates and
# rewrites), rows here outlive session deletion in Firestore — that is the
# point. Schema mirrors RUN_LOG_FIELDS in src/syros/analytics.py; extend both
# together.
#
# deletion_protection blocks `terraform destroy` on purpose — an audit log
# should not evaporate with the rest of the deployment. Note the dataset above
# sets delete_contents_on_destroy for the disposable snapshots: an operator who
# clears this flag to unblock a destroy loses these rows too, so export them
# first if they still matter.
#
# Day-partitioned on released_at: audit queries are nearly always time-ranged
# ("what did last month cost"), and the partition filter keeps the agent-facing
# bigquery tool under its scan cap as the log grows without bound.
resource "google_bigquery_table" "run_log" {
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "run_log"
  description         = "Append-only per-run audit log, streamed by the sandbox runner at release"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "released_at"
  }
  schema = jsonencode([
    { name = "session_id", type = "STRING", mode = "REQUIRED" },
    { name = "released_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "branch", type = "STRING", mode = "NULLABLE" },
    { name = "stop_reason", type = "STRING", mode = "NULLABLE" },
    { name = "run_cost_usd", type = "FLOAT64", mode = "NULLABLE" },
    { name = "cost_usd", type = "FLOAT64", mode = "NULLABLE" },
    { name = "seq_head", type = "INT64", mode = "NULLABLE" },
    { name = "model", type = "STRING", mode = "NULLABLE" },
    { name = "workspace", type = "STRING", mode = "NULLABLE" },
    { name = "created_by", type = "STRING", mode = "NULLABLE" },
    { name = "workflow", type = "STRING", mode = "NULLABLE" },
    { name = "run_id", type = "STRING", mode = "NULLABLE" },
    { name = "task", type = "STRING", mode = "NULLABLE" },
    { name = "agent", type = "STRING", mode = "NULLABLE" },
    { name = "trigger", type = "STRING", mode = "NULLABLE" },
  ])
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

# Workflow chaining: when a task session finishes, its runner launches the
# next task's session by triggering a fresh execution of this same job (with
# the session id as a container override, hence runWithOverrides). Scoped to
# the one job — the runner can start more of itself, nothing else.
resource "google_cloud_run_v2_job_iam_member" "runner_runs_job" {
  name     = google_cloud_run_v2_job.runner.name
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.runner.email}"
}

# Append-only by IAM, not by convention. The sandbox agent runs as this same
# service account with an arbitrary shell, so anything the runner can do to the
# audit log, a prompt-injected agent can do too: roles/bigquery.dataEditor would
# hand it tables.delete (and terraform's deletion_protection is state-side only,
# not API-enforced). Streaming an insert needs exactly tables.updateData —
# no read, no delete, no schema rewrite, and no jobUser anywhere.
resource "google_project_iam_custom_role" "run_log_writer" {
  role_id     = "syrosRunLogWriter"
  title       = "syros run log writer"
  description = "Stream rows into the run_log audit table; no read, no delete"
  # tables.get is metadata only (schema/size, never row data) and keeps client
  # calls that resolve the table before inserting from failing.
  permissions = ["bigquery.tables.get", "bigquery.tables.updateData"]
}

# Audit logging, not an agent capability: unconditional (unlike the
# sandbox_bigquery grants below) and scoped to the one run_log table, so the
# runner can append its release rows and touch nothing else in the dataset.
resource "google_bigquery_table_iam_member" "runner_run_log" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  table_id   = google_bigquery_table.run_log.table_id
  role       = google_project_iam_custom_role.run_log_writer.id
  member     = "serviceAccount:${google_service_account.runner.email}"
}

# Granted only when the deployment opts sessions into BigQuery (the built-in
# `bigquery` MCP server), so the default runner identity still can't read a
# single table. Project-level on purpose: the point is auditing *and* ad-hoc
# analysis of the project's own data. For a narrower deployment, replace these
# with a google_bigquery_dataset_iam_member on google_bigquery_dataset.analytics
# (dataViewer) plus jobUser alone.
resource "google_project_iam_member" "runner_bigquery_jobs" {
  count   = var.sandbox_bigquery ? 1 : 0
  project = var.project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_bigquery_data" {
  count   = var.sandbox_bigquery ? 1 : 0
  project = var.project
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.runner.email}"
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
  # keyed off the static catalog, not the secret resources: deriving for_each
  # keys from a resource makes every `terraform import` fail until those
  # secrets are already in state, which is exactly when you cannot afford it.
  for_each  = local.connectors
  secret_id = google_secret_manager_secret.connectors[each.key].id
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
        # Where the runner finds itself: workflow advancement triggers the
        # next task's execution of this same job from inside the sandbox.
        env {
          name  = "SYROS_REGION"
          value = var.region
        }
        env {
          name  = "SYROS_JOB"
          value = var.job_name
        }
        env {
          name  = "CLOUD_ML_REGION"
          value = var.vertex_region
        }
        env {
          name  = "SYROS_MODEL_BACKEND"
          value = var.model_backend
        }
        # Inert without the sandbox_bigquery IAM grants below; set
        # unconditionally so the job spec stays stable when the flag flips.
        env {
          name  = "SYROS_DATASET"
          value = var.dataset_id
        }
        env {
          name  = "SYROS_BQ_MAX_BYTES"
          value = tostring(var.bq_max_bytes)
        }
        env {
          name  = "SYROS_BQ_MAX_ROWS"
          value = tostring(var.bq_max_rows)
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

      # ALL_TRAFFIC forces every packet — Google APIs included — through the
      # default-deny VPC in network.tf; see the egress rules there.
      dynamic "vpc_access" {
        for_each = var.egress_control ? [1] : []
        content {
          egress = "ALL_TRAFFIC"
          network_interfaces {
            network    = google_compute_network.egress[0].id
            subnetwork = google_compute_subnetwork.runner[0].id
          }
        }
      }
    }
  }

  # The secretAccessor binding must exist before Cloud Run validates the mount,
  # or the job update fails with a permission error on the secret. The NAT and
  # firewall association aren't referenced from the job spec, so order them
  # explicitly — a job attached to a VPC with no NAT or policy yet would run
  # its first sessions with the wrong egress.
  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.runner_anthropic_key,
    google_compute_router_nat.egress,
    google_compute_network_firewall_policy_association.egress,
  ]

  # Releases (`make deploy`, the Release workflow) pin the image by digest via
  # `gcloud run jobs update`; var.image is only the bootstrap tag. Ignore that
  # drift — and the client stamp gcloud leaves — so a post-release apply
  # doesn't roll the pin back to :latest.
  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

# --- the console: same image, IAM-protected Cloud Run service ---
# Two access modes, both IAM-gated:
#   console_iap = true (default): public URL behind Identity-Aware Proxy —
#     open the service URL in a browser, sign in with Google, and IAP admits
#     only principals in console_invokers.
#   console_iap = false: no unauthenticated path at all; connect with
#     `gcloud run services proxy syros-console`.

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
  # static keys, per the note on runner_connectors above
  for_each  = local.connectors
  secret_id = google_secret_manager_secret.connectors[each.key].id
  role      = "roles/secretmanager.viewer"
  member    = "serviceAccount:${google_service_account.console.email}"
}

# Who may open the console. No allUsers here by design: with IAP on, the
# proxy authenticates the browser session and checks the accessor binding
# below; with IAP off, reach it with `gcloud run services proxy`, which
# authenticates as the caller.
resource "google_cloud_run_v2_service_iam_member" "console_invokers" {
  for_each = toset(var.console_invokers)
  name     = google_cloud_run_v2_service.console.name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

# IAP terminates the browser's Google sign-in and then invokes the service as
# its own service agent, so that agent — and only it beyond console_invokers —
# needs run.invoker. The identity resource forces the agent's creation, which
# enabling the API alone does not guarantee.
resource "google_project_service_identity" "iap" {
  count      = var.console_iap ? 1 : 0
  provider   = google-beta
  service    = "iap.googleapis.com"
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "console_iap_agent" {
  count    = var.console_iap ? 1 : 0
  name     = google_cloud_run_v2_service.console.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_project_service_identity.iap[0].email}"
}

# Who may pass through IAP to the console — the browser-facing counterpart of
# the run.invoker binding above, scoped to this one service.
resource "google_iap_web_cloud_run_service_iam_member" "console_accessors" {
  for_each               = var.console_iap ? toset(var.console_invokers) : toset([])
  project                = var.project
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.console.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

# --- the scheduler: Cloud Scheduler fires `syros tick` on a fixed cadence ---
# The tick reads workflows/ from Firestore, repairs active runs, fires whatever
# is due (create the run, launch its root tasks, trigger the runner job) and
# exits. It is idempotent — a slot is consumed by a Firestore transaction — so
# overlap and retry are safe.

resource "google_service_account" "scheduler" {
  account_id   = "syros-scheduler"
  display_name = "syros workflow tick"
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

      # Same lockdown as the runner: the tick only needs Firestore and
      # run.googleapis.com, both served by Private Google Access, so this
      # costs nothing and keeps one trust boundary for the whole image.
      dynamic "vpc_access" {
        for_each = var.egress_control ? [1] : []
        content {
          egress = "ALL_TRAFFIC"
          network_interfaces {
            network    = google_compute_network.egress[0].id
            subnetwork = google_compute_subnetwork.runner[0].id
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_compute_router_nat.egress,
    google_compute_network_firewall_policy_association.egress,
  ]
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
  description = "Advance and fire due syros workflows (the tick cadence bounds schedule granularity)"

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
  iap_enabled         = var.console_iap

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

  # Digest-pinned by releases, same as the runner job above.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}
