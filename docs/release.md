# Releases

Releases are cut by pushing a git tag:

```sh
git tag v0.2.0
git push origin v0.2.0
```

The `Release` workflow (`.github/workflows/release.yml`) then:

1. runs lint and the test suite,
2. builds the runner image and pushes it to Artifact Registry as
   `runner:<tag>` and `runner:latest`,
3. updates the `syros-runner` Cloud Run job and the `syros-console`
   service to the new image **digest** (Cloud Run resolves tags at
   deploy time, so pushing `:latest` alone would not roll anything),
4. publishes a GitHub Release with auto-generated notes.

If the GCP secrets below are not configured, steps 2–3 are skipped and
tagging still produces a GitHub Release.

## One-time GCP setup (Workload Identity Federation)

The workflow authenticates to GCP without a service-account key, using
[Workload Identity Federation](https://github.com/google-github-actions/auth#preferred-direct-workload-identity-federation).

```sh
PROJECT=syros-505320
REPO=yoichiojima-2/syros

gcloud iam workload-identity-pools create github \
  --project="$PROJECT" --location=global

gcloud iam workload-identity-pools providers create-oidc github-actions \
  --project="$PROJECT" --location=global \
  --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '$REPO'"

gcloud iam service-accounts create github-deployer --project="$PROJECT"

SA=github-deployer@$PROJECT.iam.gserviceaccount.com
for role in roles/artifactregistry.writer roles/run.developer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA" --role="$role"
done

POOL=$(gcloud iam workload-identity-pools describe github \
  --project="$PROJECT" --location=global --format='value(name)')
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$POOL/attribute.repository/$REPO"
```

Then set two repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `$POOL/providers/github-actions` (full resource name) |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@syros-505320.iam.gserviceaccount.com` |
