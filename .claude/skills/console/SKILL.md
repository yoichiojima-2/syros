---
name: console
description: Open the deployed syros console (IAM-only Cloud Run service) via a local gcloud proxy
---

The `syros-console` Cloud Run service allows no unauthenticated access, so it must be reached through a local authenticated proxy.

1. Start the proxy in the background:
   ```sh
   gcloud run services proxy syros-console --region asia-northeast1 --project syros-505320 --port 8080
   ```
2. Wait until `curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/` returns 200, then tell the user the console is available at http://localhost:8080 (open it with `open http://localhost:8080` if they asked to see it).
3. Leave the proxy running for the session; stop it when the user is done.

If the proxy fails with an auth error, `gcloud auth login` is needed — suggest the user run `! gcloud auth login`.

For local development instead of the deployed service, run `syros console` (rebuild static assets first with `make console` if the frontend changed).
