---
name: release
description: Cut a syros release — pick the next version from commits since the last tag, push the tag, and watch the Release workflow deploy
---

Releases are tag-driven. Do NOT run `make deploy` — that is only for manual/local deploys when explicitly requested. The full flow is documented in `docs/release.md`.

1. Confirm `main` is clean and up to date, and note the last tag:
   ```sh
   git status && git fetch origin --tags && git log --oneline $(git describe --tags --abbrev=0)..HEAD
   ```
2. Pick the next semver from the commits since the last tag (breaking → major, feature → minor, fix/docs/tweak → patch). If the user named a version, use that.
3. Tag and push — this fires `.github/workflows/release.yml`:
   ```sh
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
4. Watch the run until it completes, then verify all jobs succeeded (especially "Build and deploy" — it is silently skipped if GCP secrets are missing):
   ```sh
   gh run watch $(gh run list --workflow Release --branch vX.Y.Z --json databaseId --jq '.[0].databaseId') --exit-status
   gh run view <id> --json jobs --jq '.jobs[] | "\(.name): \(.conclusion)"'
   ```
5. Report the GitHub Release URL (`gh release view vX.Y.Z --json url --jq .url`).

Notes:
- The workflow runs lint + tests, builds/pushes the runner image, rolls the `syros-runner` job and `syros-console` service by digest, and publishes a GitHub Release.
- `pyproject.toml`'s `version` is not kept in sync with tags — do not bump it as part of a release.
