.PHONY: console test deploy

IMAGE  ?= asia-northeast1-docker.pkg.dev/syros-505320/syros/runner:latest
REGION ?= asia-northeast1
JOB    ?= syros-runner

# Rebuild the web console (Node required); output is committed into
# src/syros/console/static/ so pip installs and Docker need no Node.
console:
	cd console && npm install && npm run build

test:
	uv run pytest tests/ -q

# Build and push the runner image, then re-pin the Cloud Run job to the new
# digest — pushing :latest alone is not enough, Cloud Run resolves the tag to
# a digest at deploy time, not per execution.
deploy:
	docker build --platform linux/amd64 -t $(IMAGE) .
	docker push $(IMAGE)
	gcloud run jobs update $(JOB) --image $(IMAGE) --region $(REGION)
