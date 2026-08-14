project = "syros-505320"
image   = "asia-northeast1-docker.pkg.dev/syros-505320/syros/runner:latest"

# Escape hatch while the Vertex Claude quota request is pending: the sandbox
# calls the Anthropic API directly, so model traffic leaves GCP. Revert to
# "vertex" once quota lands.
model_backend = "anthropic"

# Console access: public URL behind IAP (console_iap defaults to true);
# these principals both pass IAP and hold run.invoker.
console_invokers = [
  "user:yoichiojima@gmail.com",
  "user:yuri.kt35@gmail.com",
]
