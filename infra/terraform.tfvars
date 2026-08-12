project = "syros-505320"
image   = "asia-northeast1-docker.pkg.dev/syros-505320/syros/runner:latest"

# Escape hatch while the Vertex Claude quota request is pending: the sandbox
# calls the Anthropic API directly, so model traffic leaves GCP. Revert to
# "vertex" once quota lands.
model_backend = "anthropic"
