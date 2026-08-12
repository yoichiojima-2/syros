output "bucket" {
  value = google_storage_bucket.sessions.name
}

output "job" {
  value = google_cloud_run_v2_job.runner.name
}

output "runner_service_account" {
  value = google_service_account.runner.email
}

output "console_url" {
  value = google_cloud_run_v2_service.console.uri
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.syros.repository_id}"
}
