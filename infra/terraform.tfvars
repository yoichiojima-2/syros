project = "syros-505320"
image   = "asia-northeast1-docker.pkg.dev/syros-505320/syros/runner:latest"

# Escape hatch while the Vertex Claude quota request is pending: the sandbox
# calls the Anthropic API directly, so model traffic leaves GCP. Revert to
# "vertex" once quota lands.
model_backend = "anthropic"

# The `analyst` agent queries BigQuery, so the runner identity needs the grants
# behind this flag: project-level roles/bigquery.jobUser (bigquery.jobs.create —
# without it every query, even `SELECT 1`, comes back 403) plus dataViewer, i.e.
# read on every dataset in this project for every session. Accepted here because
# this project's BigQuery data and its agents are the same trust boundary; see
# "The BigQuery widening" in README.md before copying this into another one.
# Read only — flip sandbox_bigquery_write to let sessions keep their own tables.
sandbox_bigquery = true

# Console access: public URL behind IAP (console_iap defaults to true);
# these principals both pass IAP and hold run.invoker.
console_invokers = [
  "user:yoichiojima@gmail.com",
  "user:yuri.kt35@gmail.com",
]
