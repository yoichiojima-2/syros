.PHONY: console test deploy deploy-console

IMAGE   ?= asia-northeast1-docker.pkg.dev/syros-505320/syros/runner:latest
REGION  ?= asia-northeast1
JOB     ?= syros-runner
SERVICE ?= syros-console

# Rebuild the web console (Node required); output is committed into
# src/syros/console/static/ so pip installs and Docker need no Node.
console:
	cd console && npm install && npm run build

test:
	uv run pytest tests/ -q

# Build and push the runner image, then re-pin the Cloud Run job and the console
# service to the new digest — pushing :latest alone is not enough, Cloud Run
# resolves the tag to a digest at deploy time, not per execution. The console
# runs the same image (different entrypoint), so both move together.
deploy:
	docker build --platform linux/amd64 -t $(IMAGE) .
	docker push $(IMAGE)
	gcloud run jobs update $(JOB) --image $(IMAGE) --region $(REGION)
	gcloud run services update $(SERVICE) --image $(IMAGE) --region $(REGION)

# Frontend-only change: rebuild the bundle, then ship it to the console service.
deploy-console: console
	docker build --platform linux/amd64 -t $(IMAGE) .
	docker push $(IMAGE)
	gcloud run services update $(SERVICE) --image $(IMAGE) --region $(REGION)
